"""Saved capture sessions: read Kismet databases for browsing (and, later, the map).

Each finished session is a Kismet database in the log directory. Parsing a long
drive's device records takes a while on a Pi, so the result is cached as compact
JSON under the state directory and only re-read when the database changes.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .state import Device, classify_crypt

log = logging.getLogger(__name__)

CACHE_VERSION = 1
ACTIVE_WINDOW = 90  # s; a database modified this recently may still be open
PHY = {"IEEE802.11": "wifi", "Bluetooth": "bt"}


@dataclass
class SavedDevice(Device):
    dev_type: str = ""  # Kismet's label, e.g. "Wi-Fi AP", "BTLE", "BR/EDR"
    packets: int = 0
    frequency: int = 0  # kHz
    wps: bool = False
    sessions: int = 1  # how many sessions saw it (merged views)


@dataclass
class SessionInfo:
    name: str
    size: int
    start: float  # earliest first-seen time (epoch s; wrong if the clock wasn't set)
    end: float
    wifi: int
    bt: int
    open: int
    located: int  # devices with a GPS position

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


# --- parsing -------------------------------------------------------------------------


def _geopoint(loc) -> tuple[float, float] | None:
    """Kismet geopoints are [lon, lat]; (0, 0) means no fix."""
    try:
        lon, lat = loc["kismet.common.location.geopoint"]
    except (KeyError, TypeError, ValueError):
        return None
    return (lat, lon) if (lat, lon) != (0, 0) else None


def parse_kismet_db(path: Path) -> tuple[SessionInfo, list[SavedDevice]]:
    # immutable=1: the file is finished, so skip locking and never create a journal.
    con = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    devices: list[SavedDevice] = []
    try:
        rows = con.execute(
            "select devkey, phyname, devmac, strongest_signal, avg_lat, avg_lon, first_time, last_time, type, device from devices"
        )
        for key, phyname, mac, strongest, avg_lat, avg_lon, first, last, dev_type, blob in rows:
            phy = PHY.get(phyname)
            if phy is None:
                continue
            try:
                d = json.loads(blob)
            except (TypeError, ValueError):
                continue
            base = lambda k, default=None: d.get("kismet.device.base." + k, default)  # noqa: E731
            signal = base("signal") or {}
            if phy == "wifi":
                record = (d.get("dot11.device") or {}).get("dot11.device.last_beaconed_ssid_record") or {}
                record = record if isinstance(record, dict) else {}
                name = record.get("dot11.advertisedssid.ssid") or ""
                crypt_raw = record.get("dot11.advertisedssid.crypt_string") or base("crypt", "")
                wps = record.get("dot11.advertisedssid.wps_state") is not None
            else:
                name, crypt_raw, wps = base("name", ""), "", False
            # Wi-Fi APs record where their signal peaked: the best guess at where they are.
            point = _geopoint(signal.get("kismet.common.signal.peak_loc")) if isinstance(signal, dict) else None
            if point is None and (avg_lat or avg_lon):
                point = (avg_lat, avg_lon)
            best = strongest or (signal.get("kismet.common.signal.max_signal", 0) if isinstance(signal, dict) else 0)
            devices.append(
                SavedDevice(
                    key=key,
                    mac=mac,
                    phy=phy,
                    name=name if isinstance(name, str) else "",
                    channel=str(base("channel", "")),
                    crypt=classify_crypt(crypt_raw or "", phy),
                    crypt_raw=crypt_raw or "",
                    manuf=base("manuf", "") or "",
                    signal=int(best or 0),
                    first_seen=float(first or 0),
                    last_seen=float(last or 0),
                    lat=point[0] if point else 0.0,
                    lon=point[1] if point else 0.0,
                    dev_type=dev_type or "",
                    packets=int(base("packets.total", 0) or 0),
                    frequency=int(base("frequency", 0) or 0),
                    wps=wps,
                )
            )
    finally:
        con.close()
    times = [d.first_seen for d in devices if d.first_seen] + [d.last_seen for d in devices if d.last_seen]
    info = SessionInfo(
        name=path.stem,
        size=path.stat().st_size,
        start=min(times, default=0.0),
        end=max(times, default=0.0),
        wifi=sum(1 for d in devices if d.phy == "wifi"),
        bt=sum(1 for d in devices if d.phy == "bt"),
        open=sum(1 for d in devices if d.phy == "wifi" and d.crypt == "OPEN"),
        located=sum(1 for d in devices if d.lat or d.lon),
    )
    return info, devices


def merge_devices(groups: list[list[SavedDevice]]) -> list[SavedDevice]:
    """One entry per device across sessions, keeping the strongest sighting's details."""
    merged: dict[tuple[str, str], SavedDevice] = {}
    for devices in groups:
        for d in devices:
            k = (d.phy, d.mac.upper())
            cur = merged.get(k)
            if cur is None:
                merged[k] = dataclasses.replace(d)
                continue
            stronger = bool(d.signal) and (not cur.signal or d.signal > cur.signal)
            keep = dataclasses.replace(d) if stronger else cur
            other = cur if stronger else d
            firsts = [t for t in (cur.first_seen, d.first_seen) if t]
            keep.first_seen = min(firsts) if firsts else 0.0
            keep.last_seen = max(cur.last_seen, d.last_seen)
            keep.packets = cur.packets + d.packets
            keep.sessions = cur.sessions + d.sessions
            keep.name = keep.name or other.name
            if not (keep.lat or keep.lon):
                keep.lat, keep.lon = other.lat, other.lon
            merged[k] = keep
    return list(merged.values())


# --- library -------------------------------------------------------------------------


class SessionLibrary:
    def __init__(self, log_dir: str | Path, cache_dir: str | Path, is_capturing: Callable[[], bool] = lambda: False):
        self.log_dir = Path(log_dir)
        self.cache_dir = Path(cache_dir)
        self.is_capturing = is_capturing

    def _databases(self) -> list[Path]:
        try:
            dbs = sorted((p for p in self.log_dir.glob("*.kismet") if p.is_file()), key=lambda p: p.stat().st_mtime)
        except OSError:
            return []
        now = time.time()
        finished = []
        for i, p in enumerate(dbs):
            if p.with_name(p.name + "-journal").exists():
                continue  # Kismet still has it open
            newest = i == len(dbs) - 1
            if newest and (self.is_capturing() or now - p.stat().st_mtime < ACTIVE_WINDOW):
                continue
            finished.append(p)
        return finished

    def _load(self, db: Path) -> tuple[SessionInfo, list[SavedDevice]]:
        st = db.stat()
        cache = self.cache_dir / f"{db.stem}.json"
        try:
            data = json.loads(cache.read_text())
            if data.get("version") == CACHE_VERSION and data.get("size") == st.st_size and data.get("mtime") == st.st_mtime:
                return SessionInfo(**data["info"]), [SavedDevice(**d) for d in data["devices"]]
        except (OSError, ValueError, TypeError, KeyError):
            pass
        info, devices = parse_kismet_db(db)
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            tmp = cache.with_suffix(".tmp")
            tmp.write_text(json.dumps({
                "version": CACHE_VERSION, "size": st.st_size, "mtime": st.st_mtime,
                "info": dataclasses.asdict(info), "devices": [dataclasses.asdict(d) for d in devices],
            }))
            tmp.replace(cache)
        except OSError as exc:
            log.warning("could not cache %s: %s", db.name, exc)
        return info, devices

    def sessions(self) -> list[SessionInfo]:
        """Finished sessions, newest first. Unreadable databases are skipped."""
        out = []
        for db in self._databases():
            try:
                out.append(self._load(db)[0])
            except sqlite3.Error as exc:
                log.warning("skipping %s: %s", db.name, exc)
        return list(reversed(out))

    def devices(self, name: str | None) -> list[SavedDevice]:
        """Devices from one session, or merged across all sessions when name is None."""
        dbs = self._databases()
        if name is not None:
            dbs = [db for db in dbs if db.stem == name]
        groups = []
        for db in dbs:
            try:
                groups.append(self._load(db)[1])
            except sqlite3.Error as exc:
                log.warning("skipping %s: %s", db.name, exc)
        return groups[0] if name is not None and groups else merge_devices(groups)


def sort_devices(devices: list[Device], sort: str) -> list[Device]:
    if sort == "signal":
        return sorted(devices, key=lambda d: (d.signal == 0, -d.signal))
    if sort == "name":
        return sorted(devices, key=lambda d: (not d.name, d.label.lower()))
    return sorted(devices, key=lambda d: -d.first_seen)  # "new"
