"""Finished capture sessions and uploading them to WiGLE or a home server."""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests

from .config import Config
from .state import State

log = logging.getLogger(__name__)

WIGLE_UPLOAD_URL = "https://api.wigle.net/api/v2/file/upload"
WIGLE_PROFILE_URL = "https://api.wigle.net/api/v2/profile/user"
# Cloudflare caps request bodies at 100 MB; the server enforces WARDRIVE_MAX_BYTES (95 MiB).
HOME_MAX_BYTES = 95 * 1024 * 1024
ACTIVE_WINDOW = 90  # seconds; a log touched this recently may still be open


@dataclass
class LogSession:
    name: str  # e.g. wardrive-20260912-20-04-41-1
    files: list[Path] = field(default_factory=list)
    wigle_rows: int = 0
    size: int = 0
    mtime: float = 0.0

    @property
    def wiglecsv(self) -> Path | None:
        return next((f for f in self.files if f.suffix == ".wiglecsv"), None)

    @property
    def started(self) -> str:
        # Kismet log_template %n-%D-%t-%i -> name-YYYYMMDD-HH-MM-SS-N (UTC)
        try:
            parts = self.name.rsplit("-", 5)
            dt = datetime.strptime(f"{parts[1]}{parts[2]}{parts[3]}{parts[4]}", "%Y%m%d%H%M%S")
            return dt.replace(tzinfo=timezone.utc).astimezone().strftime("%m/%d %H:%M")
        except (IndexError, ValueError):
            return self.name


class UploadManager:
    TARGETS = ("wigle", "home")

    def __init__(self, cfg: Config, state: State):
        self.cfg, self.state = cfg, state
        self.log_dir = Path(cfg.log_dir)
        self.record_path = cfg.state_path / "uploads.json"
        self._lock = threading.Lock()

    # --- bookkeeping -------------------------------------------------------------

    def _load_record(self) -> dict:
        try:
            return json.loads(self.record_path.read_text())
        except (OSError, ValueError):
            return {}

    def _mark(self, session: str, target: str) -> None:
        record = self._load_record()
        record.setdefault(session, {})[target] = datetime.now().isoformat(timespec="seconds")
        self.record_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.record_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(record, indent=2))
        tmp.replace(self.record_path)

    def sessions(self) -> list[LogSession]:
        """Finished sessions, newest first."""
        groups: dict[str, LogSession] = {}
        now = time.time()
        try:
            paths = [p for p in self.log_dir.iterdir() if p.is_file()]
        except OSError:
            return []
        for p in paths:
            if p.suffix not in (".kismet", ".wiglecsv", ".kismet-journal"):
                continue
            s = groups.setdefault(p.stem, LogSession(p.stem))
            s.files.append(p)
            st = p.stat()
            s.size += st.st_size
            s.mtime = max(s.mtime, st.st_mtime)
        done = []
        for s in groups.values():
            if any(f.suffix == ".kismet-journal" for f in s.files) or now - s.mtime < ACTIVE_WINDOW:
                continue  # still being written
            if s.wiglecsv:
                with s.wiglecsv.open("rb") as fh:
                    s.wigle_rows = max(0, sum(1 for _ in fh) - 2)  # two header lines
            done.append(s)
        return sorted(done, key=lambda s: s.name, reverse=True)

    def uploaded(self, session: str, target: str) -> bool:
        return target in self._load_record().get(session, {})

    def enabled(self, target: str) -> bool:
        if target == "wigle":
            w = self.cfg.upload.wigle
            return w.enabled and bool(w.api_name and w.api_token)
        h = self.cfg.upload.home
        return h.enabled and bool(h.url)

    def label(self, target: str) -> str:
        return "WiGLE" if target == "wigle" else self.cfg.upload.home.label

    def pending(self, target: str) -> list[LogSession]:
        record = self._load_record()
        out = []
        for s in self.sessions():
            if target in record.get(s.name, {}):
                continue
            if target == "wigle" and s.wigle_rows == 0:
                continue  # nothing geolocated; WiGLE would reject it
            out.append(s)
        return out

    # --- uploading ---------------------------------------------------------------

    def upload(self, target: str) -> None:
        if self.state.upload_busy:
            return
        threading.Thread(target=self._run, args=(target,), name=f"upload-{target}", daemon=True).start()

    def _run(self, target: str) -> None:
        st = self.state
        with self._lock:
            st.upload_busy = True
            try:
                if not self.enabled(target):
                    st.upload_status = f"{self.label(target)} not configured"
                    return
                todo = self.pending(target)
                if not todo:
                    st.upload_status = f"{self.label(target)}: nothing to upload"
                    return
                ok = 0
                for i, session in enumerate(todo, 1):
                    st.upload_status = f"{self.label(target)}: {i}/{len(todo)} {session.name}"
                    try:
                        (self._wigle if target == "wigle" else self._home)(session)
                    except Exception as exc:  # network, HTTP, API errors
                        st.upload_status = f"{self.label(target)} failed: {exc}"
                        st.log("error", st.upload_status)
                        return
                    self._mark(session.name, target)
                    ok += 1
                st.upload_status = f"{self.label(target)}: uploaded {ok} session(s)"
                st.log("good", st.upload_status)
            finally:
                st.upload_busy = False

    def _wigle(self, session: LogSession) -> None:
        w = self.cfg.upload.wigle
        csv = session.wiglecsv
        with csv.open("rb") as fh:
            resp = requests.post(
                WIGLE_UPLOAD_URL,
                auth=(w.api_name, w.api_token),
                files={"file": (csv.name + ".csv", fh, "text/csv")},
                data={"donate": "on" if w.donate else "off"},
                timeout=120,
            )
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}")
        body = resp.json()
        if not body.get("success", False):
            raise RuntimeError(body.get("message") or "WiGLE rejected upload")

    def _home_headers(self) -> dict[str, str]:
        h = self.cfg.upload.home
        headers = {str(k): str(v) for k, v in h.headers.items()}
        if h.token:
            headers["Authorization"] = f"Bearer {h.token}"
        return headers

    def _home(self, session: LogSession) -> None:
        """Gzip each session file and POST it as a raw body.

        Protocol (the /wardrive/upload endpoint of the home API):
          POST <url>?session=<session>&file=<name>.gz
          Content-Type: application/gzip, X-Content-SHA256: <hex of the gzip>
        Re-sending an identical file is harmless (the server answers 200).
        """
        h = self.cfg.upload.home
        tmp_dir = self.cfg.state_path / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        for f in session.files:
            with tempfile.TemporaryFile(dir=tmp_dir) as gz_file:
                with f.open("rb") as src, gzip.GzipFile(
                    filename=f.name, mode="wb", fileobj=gz_file, compresslevel=6, mtime=int(f.stat().st_mtime)
                ) as gz:
                    shutil.copyfileobj(src, gz, 1 << 20)
                size = gz_file.tell()
                if size > HOME_MAX_BYTES:
                    raise RuntimeError(f"{f.name} is {size / 1e6:.0f} MB compressed, over the {HOME_MAX_BYTES / 1e6:.0f} MB limit")
                gz_file.seek(0)
                digest = hashlib.sha256()
                for chunk in iter(lambda: gz_file.read(1 << 20), b""):
                    digest.update(chunk)
                gz_file.seek(0)
                resp = requests.post(
                    h.url,
                    params={"session": session.name, "file": f.name + ".gz"},
                    headers={
                        **self._home_headers(),
                        "Content-Type": "application/gzip",
                        "Content-Length": str(size),
                        "X-Content-SHA256": digest.hexdigest(),
                    },
                    data=gz_file,
                    timeout=(15, 600),
                    allow_redirects=False,
                )
            _api_result(resp, f.name)

    def test_connections(self) -> list[str]:
        """Check credentials for each configured target without uploading anything."""
        lines = []
        h = self.cfg.upload.home
        if not self.enabled("home"):
            lines.append(f"{h.label}: not configured (upload.home.enabled/url)")
        else:
            try:
                resp = requests.get(
                    urljoin(h.url, "/service/actions"), headers=self._home_headers(), timeout=15, allow_redirects=False
                )
                body = _api_result(resp, "service/actions")
                allowed = body.get("allowed", [])
                verdict = "OK" if "wardrive.upload" in allowed else "token works but is NOT scoped for wardrive.upload"
                lines.append(f"{h.label}: {verdict} (identity {body.get('identity')}, allowed {allowed})")
            except Exception as exc:
                lines.append(f"{h.label}: FAILED: {exc}")
        w = self.cfg.upload.wigle
        if not self.enabled("wigle"):
            lines.append("WiGLE: not configured (upload.wigle.enabled/api_name/api_token)")
        else:
            try:
                resp = requests.get(WIGLE_PROFILE_URL, auth=(w.api_name, w.api_token), timeout=15)
                body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                # 200 means the API name/token pair was accepted; don't depend on the body's shape.
                if resp.status_code == 200 and body.get("success", True):
                    lines.append(f"WiGLE: OK (user {body.get('userid') or body.get('user') or '?'})")
                else:
                    lines.append(f"WiGLE: FAILED: HTTP {resp.status_code} {body.get('message', '')}".rstrip())
            except Exception as exc:
                lines.append(f"WiGLE: FAILED: {exc}")
        return lines


def _api_result(resp: requests.Response, what: str) -> dict:
    """Parse a home API JSON reply, turning Cloudflare Access failures into clear errors."""
    if 300 <= resp.status_code < 400:
        raise RuntimeError(
            f"{what}: redirected to a login page. Cloudflare Access did not accept the service token "
            "(check CF-Access-Client-Id/Secret and that the policy action is Service Auth)"
        )
    try:
        body = resp.json()
    except ValueError:
        raise RuntimeError(f"{what}: HTTP {resp.status_code}, not a JSON reply (blocked by Cloudflare Access?)") from None
    if resp.status_code != 200 or not body.get("ok"):
        raise RuntimeError(f"{what}: HTTP {resp.status_code}: {body.get('error', 'request failed')}")
    return body
