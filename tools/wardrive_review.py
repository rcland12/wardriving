#!/usr/bin/env python3
"""Review archived wardrive sessions, repair known problems, then publish to WiGLE.

Sessions are the directories the home API stores uploads in:

    <data>/<session>/<session>.wiglecsv.gz   the Pi's live WiGLE CSV
    <data>/<session>/<session>.kismet.gz     the Kismet database (used for repairs)
    <data>/<session>/review/                 written by this tool; originals are never touched

Typical flow:

    wardrive_review.py list
    wardrive_review.py check latest
    wardrive_review.py fix latest          # writes review/<session>.wiglecsv
    wardrive_review.py check latest        # now checks the repaired file
    wardrive_review.py wigle latest        # uploads the reviewed file

Settings come from the environment or ~/.config/wardrive/review.env (KEY=VALUE):

    WARDRIVE_DATA     session directory (or pass --data)
    WIGLE_API_NAME    from https://wigle.net/account ("API Name", starts with AID)
    WIGLE_API_TOKEN   ("API Token", not the "Encoded for use" value)
    WIGLE_DONATE      on/off: allow WiGLE commercial use of uploaded data (default off)

Standard library only, so it runs on any Python 3.10+.
"""

from __future__ import annotations

import argparse
import base64
import dataclasses
import gzip
import hashlib
import json
import math
import os
import re
import sqlite3
import statistics
import sys
import tempfile
import urllib.error
import urllib.request
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ENV_FILE = Path("~/.config/wardrive/review.env").expanduser()
WIGLE_API = "https://api.wigle.net/api/v2"
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
STEP_SECONDS = 1800  # a clock change at least this large is treated as a step (the Pi was off for a while)
MAX_SPREAD_KM = 300  # a single drive spanning more than this suggests a GPS glitch
N_FIELDS = 11  # WigleWifi-1.4 as written by Kismet


# --- settings ------------------------------------------------------------------


def load_env_file(path: Path = ENV_FILE) -> None:
    """Fill os.environ from a KEY=VALUE file without overriding real environment vars."""
    if not path.is_file():
        return
    if path.stat().st_mode & 0o077:
        print(f"warning: {path} is readable by other users; run: chmod 600 {path}", file=sys.stderr)
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


# --- WiGLE CSV parsing ---------------------------------------------------------


@dataclasses.dataclass
class Row:
    mac: str
    ssid: str
    auth: str
    first_seen: str
    channel: str
    rssi: str
    lat: str
    lon: str
    alt: str
    accuracy: str
    type: str

    @classmethod
    def parse(cls, line: str) -> "Row | None":
        parts = line.split(",")
        if len(parts) < N_FIELDS:
            return None
        # Kismet does not quote fields, so an SSID containing commas spreads over
        # several parts; everything around it has a fixed position.
        return cls(parts[0], ",".join(parts[1 : len(parts) - 9]), *parts[-9:])

    def line(self) -> str:
        return ",".join(dataclasses.astuple(self))

    @property
    def time(self) -> datetime | None:
        try:
            return datetime.strptime(self.first_seen, TIME_FORMAT).replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    @property
    def coords(self) -> tuple[float, float] | None:
        try:
            lat, lon = float(self.lat), float(self.lon)
        except ValueError:
            return None
        if (lat, lon) == (0.0, 0.0) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return None
        return lat, lon


@dataclasses.dataclass
class WigleCsv:
    preheader: str
    header: str
    rows: list[Row]
    bad_lines: int

    @classmethod
    def parse(cls, text: str) -> "WigleCsv":
        lines = text.splitlines()
        if len(lines) < 2 or not lines[0].startswith("WigleWifi"):
            raise ValueError("not a WiGLE CSV (missing WigleWifi pre-header)")
        rows, bad = [], 0
        for line in lines[2:]:
            if not line.strip():
                continue
            row = Row.parse(line)
            if row is None:
                bad += 1
            else:
                rows.append(row)
        return cls(lines[0], lines[1], rows, bad)

    def text(self) -> str:
        return "\n".join([self.preheader, self.header, *(r.line() for r in self.rows)]) + "\n"


# --- sessions ------------------------------------------------------------------


class Session:
    def __init__(self, path: Path):
        self.path = path
        self.name = path.name
        self.review_dir = path / "review"
        self.reviewed_csv = self.review_dir / f"{self.name}.wiglecsv"
        self.record_path = self.review_dir / "review.json"

    def _find(self, suffix: str) -> Path | None:
        for candidate in (self.path / f"{self.name}{suffix}.gz", self.path / f"{self.name}{suffix}"):
            if candidate.is_file():
                return candidate
        return None

    @property
    def original_csv(self) -> Path | None:
        return self._find(".wiglecsv")

    @property
    def kismet_db(self) -> Path | None:
        return self._find(".kismet")

    @property
    def uploaded_at(self) -> float:
        files = [p for p in self.path.iterdir() if p.is_file()]
        return max((p.stat().st_mtime for p in files), default=0.0)

    def read_original(self) -> WigleCsv:
        path = self.original_csv
        if path is None:
            raise FileNotFoundError(f"{self.name}: no .wiglecsv file")
        raw = gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()
        return WigleCsv.parse(raw.decode("utf-8", errors="replace"))

    def read_current(self) -> tuple[WigleCsv, bool]:
        """The reviewed CSV if one exists, else the original. Second value: is reviewed."""
        if self.reviewed_csv.is_file():
            return WigleCsv.parse(self.reviewed_csv.read_text()), True
        return self.read_original(), False

    def record(self) -> dict:
        try:
            return json.loads(self.record_path.read_text())
        except (OSError, ValueError):
            return {}

    def save_record(self, record: dict) -> None:
        self.review_dir.mkdir(exist_ok=True)
        tmp = self.record_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(record, indent=2) + "\n")
        tmp.replace(self.record_path)


def all_sessions(data: Path) -> list[Session]:
    """Sessions oldest first by when they were uploaded. Names come from the Pi's clock at
    capture start, which can be wrong (no RTC), so they don't sort reliably."""
    if not data.is_dir():
        raise SystemExit(f"data directory not found: {data}")
    return sorted((Session(p) for p in data.iterdir() if p.is_dir()), key=lambda s: (s.uploaded_at, s.name))


def find_session(data: Path, ref: str) -> Session:
    sessions = all_sessions(data)
    if not sessions:
        raise SystemExit(f"no sessions in {data}")
    if ref == "latest":
        return sessions[-1]
    exact = [s for s in sessions if s.name == ref]
    matches = exact or [s for s in sessions if ref in s.name]
    if len(matches) != 1:
        names = ", ".join(s.name for s in matches) or "none"
        raise SystemExit(f"session {ref!r} matches {len(matches)} sessions: {names}")
    return matches[0]


# --- Kismet database -----------------------------------------------------------


@dataclasses.dataclass
class KismetInfo:
    crypt: dict[str, str]  # BSSID -> Kismet crypt string, e.g. 'WPA2 WPA2-PSK AES-CCMP'
    wps: set[str]  # BSSIDs whose beacons carry a WPS element
    integrity: str
    steps: list[tuple[float, float, float]]  # (clock before, clock after, estimated true offset) in epoch seconds


def read_kismet_db(db_path: Path) -> KismetInfo:
    with tempfile.TemporaryDirectory() as tmp:
        path = db_path
        if db_path.suffix == ".gz":
            path = Path(tmp) / db_path.stem
            with gzip.open(db_path, "rb") as src, path.open("wb") as dst:
                while chunk := src.read(1 << 20):
                    dst.write(chunk)
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            integrity = con.execute("pragma integrity_check").fetchone()[0]
            crypt, wps = {}, set()
            for mac, blob in con.execute("select devmac, device from devices where phyname = 'IEEE802.11'"):
                try:
                    device = json.loads(blob)
                except (TypeError, ValueError):
                    continue
                record = (device.get("dot11.device") or {}).get("dot11.device.last_beaconed_ssid_record")
                record = record if isinstance(record, dict) else {}
                value = record.get("dot11.advertisedssid.crypt_string") or device.get("kismet.device.base.crypt")
                if value:
                    crypt[mac.upper()] = value
                if record.get("dot11.advertisedssid.wps_state") is not None:
                    wps.add(mac.upper())
            # Packets are stored in receive order, a fraction of a second apart, so a clock
            # step shows up as one large jump between neighbours - unlike a quiet stretch
            # with no new networks, which leaves FirstSeen gaps but no packet-order jump.
            ts = [sec + usec / 1e6 for sec, usec in con.execute("select ts_sec, ts_usec from packets order by rowid")]
        finally:
            con.close()
    return KismetInfo(crypt, wps, integrity, packet_clock_steps(ts))


def packet_clock_steps(ts: list[float]) -> list[tuple[float, float, float]]:
    """Clock steps in receive-ordered packet timestamps: (before, after, signed offset).

    Kismet's log queue occasionally writes a packet slightly out of order, so one step
    can appear as +X, -X, +X between neighbours. Jumps of the same size are grouped and
    their net direction and median size taken.
    """
    deltas = [b - a for a, b in zip(ts, ts[1:])]
    normal = [d for d in deltas if 0 <= d < STEP_SECONDS]
    typical = statistics.median(normal) if normal else 0.0
    groups: list[list[tuple[float, float, float]]] = []
    for a, b, d in zip(ts, ts[1:], deltas):
        if abs(d) < STEP_SECONDS:
            continue
        if groups and abs(abs(d) - abs(groups[-1][0][2])) < 60:
            groups[-1].append((a, b, d))
        else:
            groups.append([(a, b, d)])
    steps = []
    for group in groups:
        net = sum(d for _, _, d in group)
        size = statistics.median(abs(d) for _, _, d in group) - typical
        steps.append((group[0][0], group[-1][1], math.copysign(size, net)))
    return steps


def wigle_authmode(crypt: str, wps: bool = False) -> str:
    """Kismet crypt string -> WiGLE/Android capability string, e.g. '[WPA2-PSK-CCMP][RSN-PSK-CCMP][ESS]'."""
    tail = "[WPS][ESS]" if wps else "[ESS]"
    tokens = ["WPA" if t == "WPA1" else t for t in crypt.replace("-", " ").upper().split()]
    if not tokens or "OPEN" in tokens or crypt.strip().lower() in ("none", ""):
        return tail
    if "WEP" in tokens and not any(t.startswith("WPA") for t in tokens):
        return "[WEP]" + tail
    kms = [k for k in ("PSK", "SAE", "EAP", "OWE") if k in tokens]
    km = "+".join(kms) if kms else "UNKNOWN"
    ciphers = [c for c, names in (("CCMP", ("CCMP",)), ("GCMP", ("GCMP",)), ("TKIP", ("TKIP",))) if any(n in tokens for n in names)]
    cipher = "+".join(ciphers) if ciphers else "CCMP"
    parts = []
    if "WPA" in tokens:
        parts.append(f"[WPA-{km}-{cipher}]")
    if "WPA3" in tokens or "SAE" in kms or "OWE" in kms:
        parts.append(f"[WPA3-{km}-{cipher}][RSN-{km}-{cipher}]")
    elif "WPA2" in tokens:
        parts.append(f"[WPA2-{km}-{cipher}][RSN-{km}-{cipher}]")
    return "".join(parts) + tail


def auth_has_security(auth: str) -> bool:
    return bool(re.search(r"WPA|RSN|WEP|SAE|OWE", auth, re.IGNORECASE))


# --- checks --------------------------------------------------------------------


@dataclasses.dataclass
class ClockStep:
    boundary: datetime  # rows on the stale side of this instant carry the wrong time
    stale_earlier: bool  # True: clock was behind and jumped forward (the usual no-RTC case)
    stale_rows: int
    gap_seconds: int  # gap between the two FirstSeen clusters in the CSV
    offset_seconds: int  # correction to add to stale rows
    source: str  # how the offset was measured


def find_clock_steps(rows: list[Row], kismet: KismetInfo | None) -> list[ClockStep]:
    """Find FirstSeen clusters separated by a clock change.

    FirstSeen is when a device was *first* seen, so rows for devices discovered before
    the clock was set keep the stale time even when written later: stale and correct
    rows interleave through the file. Group by time instead, and use the rows written
    first (capture always starts on the stale clock) to tell which side is wrong.
    """
    times = sorted({r.time for r in rows if r.time})
    boundaries = [(a, b) for a, b in zip(times, times[1:]) if (b - a).total_seconds() >= STEP_SECONDS]
    if kismet is not None and not kismet.steps:
        return []  # packet order shows no clock change: the gaps are real quiet stretches
    steps = []
    first_rows = [r.time for r in rows[:25] if r.time]
    for a, b in boundaries:
        boundary = a + (b - a) / 2
        early = sum(1 for t in first_rows if t < boundary)
        stale_earlier = early >= len(first_rows) / 2
        stale_rows = sum(1 for r in rows if r.time and (r.time < boundary) == stale_earlier)
        gap = (b - a).total_seconds()
        if kismet is not None and len(kismet.steps) == len(boundaries):
            offset, source = abs(kismet.steps[len(steps)][2]), "Kismet packet timestamps, +/- 1 s"
        else:
            offset, source = gap, "gap between FirstSeen clusters (upper bound; no Kismet DB)"
        steps.append(ClockStep(boundary, stale_earlier, stale_rows, int(gap),
                               int(round(offset if stale_earlier else -offset)), source))
    return steps


def spread_km(points: list[tuple[float, float]]) -> float:
    if len(points) < 2:
        return 0.0
    lat0 = statistics.fmean(p[0] for p in points)
    lon0 = statistics.fmean(p[1] for p in points)

    def dist(p):
        dlat, dlon = math.radians(p[0] - lat0), math.radians(p[1] - lon0)
        a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat0)) * math.cos(math.radians(p[0])) * math.sin(dlon / 2) ** 2
        return 6371 * 2 * math.asin(math.sqrt(a))

    return max(dist(p) for p in points)


@dataclasses.dataclass
class Report:
    lines: list[str] = dataclasses.field(default_factory=list)
    failures: list[str] = dataclasses.field(default_factory=list)
    warnings: list[str] = dataclasses.field(default_factory=list)

    def info(self, text: str) -> None:
        self.lines.append(text)

    @property
    def verdict(self) -> str:
        return "FAIL" if self.failures else "WARN" if self.warnings else "OK"


def check(session: Session, csv: WigleCsv, reviewed: bool, kismet: KismetInfo | None) -> Report:
    rep = Report()
    crypt = kismet.crypt if kismet else None
    rows = csv.rows
    rep.info(f"file:        {'review/' + session.reviewed_csv.name if reviewed else session.original_csv.name}")
    rep.info(f"format:      {csv.preheader.split(',')[0]}, {len(rows)} rows" + (f", {csv.bad_lines} unparseable" if csv.bad_lines else ""))
    if not rows:
        rep.failures.append("no rows: nothing to upload (was there a GPS fix?)")
        return rep
    if csv.bad_lines:
        rep.warnings.append(f"{csv.bad_lines} lines could not be parsed and will be ignored by WiGLE")

    types = Counter(r.type for r in rows)
    rep.info(f"types:       {dict(types)}; unique MACs {len({r.mac for r in rows})}")

    wifi = [r for r in rows if r.type == "WIFI"]
    auth_labels = Counter(_auth_label(r.auth) for r in wifi)
    rep.info(f"wifi auth:   {dict(auth_labels.most_common())}")

    # Timestamps
    times = [r.time for r in rows]
    invalid = sum(1 for t in times if t is None)
    valid = sorted(t for t in times if t)
    now = datetime.now(timezone.utc)
    if valid:
        rep.info(f"time (UTC):  {valid[0]:%Y-%m-%d %H:%M:%S} -> {valid[-1]:%Y-%m-%d %H:%M:%S}")
    if invalid:
        rep.failures.append(f"{invalid} rows have an unreadable FirstSeen time")
    implausible = sum(1 for t in valid if t.year < 2020 or (t - now).total_seconds() > 86400)
    if implausible:
        rep.failures.append(f"{implausible} rows have implausible times (before 2020 or in the future): clock not set")
    if kismet is not None and kismet.integrity != "ok":
        rep.warnings.append(f"Kismet DB integrity check: {kismet.integrity}")
    steps = find_clock_steps(rows, kismet)
    for step in steps:
        side = "before" if step.stale_earlier else "after"
        rep.failures.append(
            f"clock was changed mid-capture: {step.stale_rows} rows have FirstSeen {side} "
            f"{step.boundary:%Y-%m-%d %H:%M} UTC and are off by {_signed_hms(step.offset_seconds)} "
            f"(measured from {step.source}). Run: fix"
        )
    if len(steps) > 1:
        rep.failures.append("more than one clock change: fix needs --offset for each case; review manually")

    # Coordinates
    points = [c for c in (r.coords for r in rows) if c]
    missing = len(rows) - len(points)
    if missing:
        rep.warnings.append(f"{missing} rows have no valid coordinates")
    if points:
        spread = spread_km(points)
        rep.info(f"area:        lat {min(p[0] for p in points):.4f}..{max(p[0] for p in points):.4f}, "
                 f"lon {min(p[1] for p in points):.4f}..{max(p[1] for p in points):.4f} (max {spread:.1f} km from center)")
        if spread > MAX_SPREAD_KM:
            rep.warnings.append(f"points spread {spread:.0f} km from their center: possible GPS glitch")

    # Security (Kismet 2025.09's live wiglecsv writer drops WPA/RSN details)
    no_security = [r for r in wifi if not auth_has_security(r.auth)]
    if crypt is None:
        if wifi and len(no_security) == len(wifi):
            rep.warnings.append("no Wi-Fi row lists any encryption and there is no Kismet DB to verify against")
    else:
        wrong = [
            r for r in wifi
            if r.mac.upper() in crypt and _auth_label(r.auth) != _auth_label(wigle_authmode(crypt[r.mac.upper()]))
        ]
        if wrong:
            rep.failures.append(f"{len(wrong)} Wi-Fi rows list different security than Kismet recorded. Run: fix")
        unknown = sum(1 for r in wifi if r.mac.upper() not in crypt)
        if unknown:
            rep.warnings.append(f"{unknown} Wi-Fi rows have no matching device in the Kismet DB")

    dupes = len(rows) - len({r.line() for r in rows})
    if dupes:
        rep.warnings.append(f"{dupes} exact duplicate rows")
    return rep


def _auth_label(auth: str) -> str:
    a = auth.upper()
    for label in ("WPA3", "WPA2", "RSN", "WPA", "WEP"):
        if label in a:
            return "WPA2" if label == "RSN" else label
    return "open"


def _signed_hms(seconds: int) -> str:
    sign = "+" if seconds >= 0 else "-"
    s = abs(seconds)
    return f"{sign}{s // 3600}h{s % 3600 // 60:02d}m{s % 60:02d}s"


def load_kismet(session: Session) -> KismetInfo | None:
    db = session.kismet_db
    return read_kismet_db(db) if db is not None else None


# --- fixing --------------------------------------------------------------------


def apply_fixes(
    csv: WigleCsv, kismet: KismetInfo | None, *, offset: int | None, drop_stale: bool, restore_auth: bool = True
) -> list[str]:
    """Repair `csv` in place. Returns human-readable descriptions of what changed."""
    notes = []
    steps = find_clock_steps(csv.rows, kismet)
    if len(steps) > 1:
        raise SystemExit(f"{len(steps)} clock changes found; this needs a manual review")
    if steps:
        step = steps[0]
        correction = step.offset_seconds if offset is None else offset

        def is_stale(r: Row) -> bool:
            t = r.time
            return t is not None and ((t < step.boundary) == step.stale_earlier)

        stale = [r for r in csv.rows if is_stale(r)]
        if drop_stale:
            csv.rows = [r for r in csv.rows if not is_stale(r)]
            notes.append(f"dropped {len(stale)} rows recorded before the clock was set")
        else:
            for r in stale:
                r.first_seen = datetime.fromtimestamp(r.time.timestamp() + correction, timezone.utc).strftime(TIME_FORMAT)
            notes.append(f"shifted {len(stale)} rows by {_signed_hms(correction)} (clock set mid-capture)")

    seen, unique = set(), []
    for r in csv.rows:
        if r.line() not in seen:
            seen.add(r.line())
            unique.append(r)
    if len(unique) != len(csv.rows):
        notes.append(f"removed {len(csv.rows) - len(unique)} exact duplicate rows")
        csv.rows = unique

    crypt = kismet.crypt if (kismet and restore_auth) else None
    if crypt:
        # Kismet 2025.09's live writer drops WPA/RSN details and flags every encrypted
        # network as WPS, so rebuild AuthMode from the database for every known BSSID.
        repaired = 0
        for r in csv.rows:
            mac = r.mac.upper()
            if r.type != "WIFI" or mac not in crypt:
                continue
            new = wigle_authmode(crypt[mac], wps=mac in kismet.wps)
            if new != r.auth:
                r.auth = new
                repaired += 1
        if repaired:
            notes.append(f"rebuilt security (AuthMode) on {repaired} Wi-Fi rows from the Kismet DB")
    return notes


# --- WiGLE API -----------------------------------------------------------------


def wigle_request(path: str, *, method: str = "GET", body: bytes | None = None, content_type: str | None = None) -> dict:
    name, token = os.environ.get("WIGLE_API_NAME", ""), os.environ.get("WIGLE_API_TOKEN", "")
    if not (name and token):
        raise SystemExit("WIGLE_API_NAME and WIGLE_API_TOKEN are not set (see --help)")
    req = urllib.request.Request(WIGLE_API + path, data=body, method=method)
    req.add_header("Authorization", "Basic " + base64.b64encode(f"{name}:{token}".encode()).decode())
    req.add_header("Accept", "application/json")
    if content_type:
        req.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        raise SystemExit(f"WiGLE {path}: HTTP {exc.code} {detail}") from None
    except urllib.error.URLError as exc:
        raise SystemExit(f"WiGLE {path}: {exc.reason}") from None


def multipart(fields: dict[str, str], file_field: str, filename: str, payload: bytes, file_type: str) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    out = []
    for key, value in fields.items():
        out.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
    out.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
        f"Content-Type: {file_type}\r\n\r\n".encode()
    )
    out.append(payload)
    out.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(out), f"multipart/form-data; boundary={boundary}"


# --- commands ------------------------------------------------------------------


def cmd_list(args) -> int:
    sessions = all_sessions(args.data)
    print(f"{'SESSION':34} {'ROWS':>5}  {'STARTED (UTC)':19}  STATUS")
    for s in sessions:
        try:
            csv, reviewed = s.read_current()
        except (OSError, ValueError) as exc:
            print(f"{s.name:34} {'-':>5}  {'-':19}  unreadable: {exc}")
            continue
        times = sorted(t for t in (r.time for r in csv.rows) if t)
        started = f"{times[0]:%Y-%m-%d %H:%M:%S}" if times else "-"
        record = s.record()
        if record.get("wigle"):
            status = "on WiGLE"
        elif reviewed:
            status = "reviewed (ready for WiGLE)"
        elif not csv.rows:
            status = "empty (no GPS rows)"
        else:
            status = "not reviewed"
        print(f"{s.name:34} {len(csv.rows):>5}  {started:19}  {status}")
    return 0


def run_check(session: Session, *, original: bool = False) -> Report:
    if original:
        csv, reviewed = session.read_original(), False
    else:
        csv, reviewed = session.read_current()
    return check(session, csv, reviewed, load_kismet(session))


def print_report(session: Session, rep: Report) -> None:
    print(f"== {session.name}")
    for line in rep.lines:
        print("  " + line)
    for text in rep.failures:
        print("  FAIL  " + text)
    for text in rep.warnings:
        print("  WARN  " + text)
    print(f"  verdict: {rep.verdict}")


def cmd_check(args) -> int:
    session = find_session(args.data, args.session)
    rep = run_check(session, original=args.original)
    print_report(session, rep)
    return 1 if rep.failures else 0


def cmd_fix(args) -> int:
    session = find_session(args.data, args.session)
    csv = session.read_original()
    kismet = load_kismet(session)
    notes = apply_fixes(csv, kismet, offset=args.offset, drop_stale=args.drop_stale, restore_auth=not args.no_auth)
    if not notes:
        print(f"{session.name}: nothing to fix")
    session.review_dir.mkdir(exist_ok=True)
    tmp = session.reviewed_csv.with_suffix(".tmp")
    tmp.write_text(csv.text())
    tmp.replace(session.reviewed_csv)
    record = session.record()
    record["reviewed"] = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fixes": notes,
        "rows": len(csv.rows),
        "sha256": hashlib.sha256(session.reviewed_csv.read_bytes()).hexdigest(),
    }
    session.save_record(record)
    for note in notes:
        print(f"  fixed: {note}")
    print(f"  wrote review/{session.reviewed_csv.name}\n")
    rep = check(session, csv, True, kismet)
    print_report(session, rep)
    return 1 if rep.failures else 0


def cmd_wigle(args) -> int:
    session = find_session(args.data, args.session)
    rep = run_check(session)
    print_report(session, rep)
    if rep.failures and not args.force:
        print("\nnot uploading: fix the FAIL items first (or pass --force)")
        return 1
    record = session.record()
    if record.get("wigle") and not args.force:
        print(f"\nalready uploaded ({record['wigle'].get('at')}); pass --force to upload again")
        return 1
    csv, reviewed = session.read_current()
    payload = csv.text().encode()
    donate = os.environ.get("WIGLE_DONATE", "off").lower() in ("on", "1", "true", "yes")
    filename = f"{session.name}.csv"
    print(f"\nuploading {filename} ({len(csv.rows)} rows, {len(payload)} bytes, "
          f"{'reviewed' if reviewed else 'ORIGINAL'} file, donate={'on' if donate else 'off'})")
    if args.dry_run:
        print("dry run: not sent")
        return 0
    body, ctype = multipart({"donate": "on" if donate else "off"}, "file", filename, payload, "text/csv")
    resp = wigle_request("/file/upload", method="POST", body=body, content_type=ctype)
    if not resp.get("success"):
        print(f"WiGLE rejected the upload: {resp.get('message') or resp}")
        return 1
    transids = [t.get("transId") for t in (resp.get("results") or {}).get("transids", [])]
    record["wigle"] = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "transids": transids,
        "rows": len(csv.rows),
        "reviewed": reviewed,
        "donate": donate,
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    session.save_record(record)
    print(f"uploaded: transaction {', '.join(filter(None, transids)) or '?'}"
          + (f" (warning: {resp['warning']})" if resp.get("warning") else ""))
    print("processing status: wardrive_review.py wigle-status")
    return 0


def cmd_wigle_status(args) -> int:
    resp = wigle_request(f"/file/transactions?pagestart=0&pageend={args.limit}")
    results = resp.get("results") or []
    print(f"WiGLE queue depth: {resp.get('processingQueueDepth', '?')}")
    print(f"{'TRANSACTION':22} {'STATUS':12} {'DONE':>5} {'NEW WIFI':>8} {'NEW BT':>6}  FILE")
    for t in results:
        print(f"{str(t.get('transid')):22} {str(t.get('status')):12} {t.get('percentDone', 0):>4.0f}% "
              f"{t.get('discoveredGps', 0):>8} {t.get('btDiscoveredGps', 0):>6}  {t.get('fileName')}")
    return 0


def cmd_wigle_test(args) -> int:
    resp = wigle_request("/profile/user")
    user = resp.get("userid") or resp.get("user") or "?"
    print(f"WiGLE credentials OK (user {user})")
    return 0


def main(argv: list[str] | None = None) -> int:
    load_env_file()
    parser = argparse.ArgumentParser(
        description="Review archived wardrive sessions and publish them to WiGLE.",
        epilog="Settings come from" + __doc__.split("Settings come from", 1)[1],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--data", type=Path, default=os.environ.get("WARDRIVE_DATA"), help="session directory (env WARDRIVE_DATA)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="list sessions and their review status").set_defaults(func=cmd_list)

    p = sub.add_parser("check", help="validate a session (the reviewed file if it exists)")
    p.add_argument("session", help="session name, unique part of one, or 'latest'")
    p.add_argument("--original", action="store_true", help="check the original upload, not the reviewed copy")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("fix", help="repair clock jumps and missing encryption; writes review/<session>.wiglecsv")
    p.add_argument("session")
    p.add_argument("--offset", type=int, help="seconds to add to the stale rows (default: measured from the Kismet DB)")
    p.add_argument("--drop-stale", action="store_true", help="drop rows recorded before the clock was set instead of shifting them")
    p.add_argument("--no-auth", action="store_true", help="don't restore encryption details from the Kismet DB")
    p.set_defaults(func=cmd_fix)

    p = sub.add_parser("wigle", help="upload a checked session to WiGLE")
    p.add_argument("session")
    p.add_argument("--dry-run", action="store_true", help="run the checks and show what would be sent")
    p.add_argument("--force", action="store_true", help="upload despite FAIL items or a previous upload")
    p.set_defaults(func=cmd_wigle)

    p = sub.add_parser("wigle-status", help="show processing status of recent WiGLE uploads")
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(func=cmd_wigle_status)

    sub.add_parser("wigle-test", help="check WiGLE credentials").set_defaults(func=cmd_wigle_test)

    args = parser.parse_args(argv)
    if args.command.startswith("wigle-"):
        return args.func(args)
    if args.data is None:
        parser.error("set WARDRIVE_DATA or pass --data")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
