"""Write a simulated drive as Kismet would: a .kismet SQLite database and a .wiglecsv log.

The files follow Kismet 2025.09's layout closely enough for the session browser, the map,
kismetdb tools and tools/wardrive_review.py. They copy two real Kismet quirks on purpose,
so the review tool's repairs get exercised: the live WiGLE CSV omits WPA/RSN details and
flags every encrypted network as WPS, and a session can start with the Pi's clock wrong.

Every file is marked as demo data (the CSV's device=wardrive-demo field and a Kismet
message), and the review tool refuses to send marked files to WiGLE.
"""

from __future__ import annotations

import json
import os
import random
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .world import SimDevice

DEMO_MARKER = "wardrive-demo"
KISMET_RELEASE = "2025.09.0"
WIGLE_PREHEADER = (
    f"WigleWifi-1.4,appRelease=Kismet{KISMET_RELEASE.replace('.', '')},model=Kismet,release={KISMET_RELEASE},"
    f"device={DEMO_MARKER},display=kismet,board=kismet,brand=kismet"
)
WIGLE_HEADER = "MAC,SSID,AuthMode,FirstSeen,Channel,RSSI,CurrentLatitude,CurrentLongitude,AltitudeMeters,AccuracyMeters,Type"
WIGLE_ROW_EVERY = 15.0  # s between CSV rows for one device
PACKET_ROW_EVERY = 10.0  # s between logged packets for one device
TRACK_EVERY = 5.0  # s between GPS breadcrumbs (Kismet's GPS snapshots)
UNFINISHED_WINDOW = 91  # s; readers treat logs modified more recently than this as still open

SCHEMA = """
CREATE TABLE KISMET (kismet_version TEXT, db_version INT, db_module TEXT);
CREATE TABLE devices (first_time INT, last_time INT, devkey TEXT, phyname TEXT, devmac TEXT, strongest_signal INT,
    min_lat REAL, min_lon REAL, max_lat REAL, max_lon REAL, avg_lat REAL, avg_lon REAL, bytes_data INT, type TEXT,
    device BLOB, UNIQUE(phyname, devmac) ON CONFLICT REPLACE);
CREATE TABLE packets (ts_sec INT, ts_usec INT, phyname TEXT, sourcemac TEXT, destmac TEXT, transmac TEXT, frequency REAL,
    devkey TEXT, lat REAL, lon REAL, alt REAL, speed REAL, heading REAL, packet_len INT, signal INT, datasource TEXT,
    dlt INT, packet BLOB, error INT, tags TEXT, datarate REAL, hash INT, packetid INT, packet_full_len INT);
CREATE TABLE data (ts_sec INT, ts_usec INT, phyname TEXT, devmac TEXT, lat REAL, lon REAL, alt REAL, speed REAL,
    heading REAL, datasource TEXT, type TEXT, json BLOB);
CREATE TABLE datasources (uuid TEXT, typestring TEXT, definition TEXT, name TEXT, interface TEXT, json BLOB,
    UNIQUE(uuid) ON CONFLICT REPLACE);
CREATE TABLE alerts (ts_sec INT, ts_usec INT, phyname TEXT, devmac TEXT, lat REAL, lon REAL, header TEXT, json BLOB);
CREATE TABLE messages (ts_sec INT, lat REAL, lon REAL, msgtype TEXT, message TEXT);
CREATE TABLE snapshots (ts_sec INT, ts_usec INT, lat REAL, lon REAL, snaptype TEXT, json BLOB);
"""
PHYNAME = {"wifi": "IEEE802.11", "bt": "Bluetooth"}


@dataclass
class Sighting:
    device: SimDevice
    first: float  # epoch s, as the (possibly wrong) clock read it
    last: float
    signal: int  # latest
    best: int
    worst: int
    peak: tuple[float, float, float]  # where the signal peaked: (lat, lon, alt)
    lat_sum: float = 0.0
    lon_sum: float = 0.0
    fixes: int = 0
    bounds: list[float] = field(default_factory=lambda: [90.0, 180.0, -90.0, -180.0])
    packets: int = 0
    bytes: int = 0
    last_row: float = -1e18
    last_packet: float = -1e18

    @property
    def location(self) -> tuple[float, float]:
        return self.peak[0], self.peak[1]


class SessionRecorder:
    """Collects what the simulated radios hear during one capture session."""

    def __init__(self, started: float, seed: int = 0, clock_error: float = 0.0, clock_fixed_after: float = 0.0):
        """clock_error: seconds the clock reads behind until `clock_fixed_after` seconds in."""
        self.started = started
        self.clock_error, self.clock_fixed_at = clock_error, started + clock_fixed_after
        self.rng = random.Random(seed)
        self.sightings: dict[str, Sighting] = {}
        self.packets: list[tuple] = []
        self.rows: list[str] = []
        self.messages: list[tuple] = []
        self.track: list[tuple] = []
        self._last_track = -1e18
        self.ended = started
        self.alfa_uuid = str(uuid.UUID(int=self.rng.getrandbits(128)))
        self.bt_uuid = str(uuid.UUID(int=self.rng.getrandbits(128)))

    def clock(self, t: float) -> float:
        return t - self.clock_error if t < self.clock_fixed_at else t

    @property
    def name(self) -> str:
        return "wardrive-" + datetime.fromtimestamp(self.clock(self.started), timezone.utc).strftime("%Y%m%d-%H-%M-%S") + "-1"

    def message(self, t: float, text: str, lat: float = 0.0, lon: float = 0.0, kind: str = "INFO") -> None:
        self.messages.append((int(self.clock(t)), lat, lon, kind, text))

    def position(self, t: float, lat: float, lon: float, alt: float, speed: float, heading: float) -> None:
        """The car's GPS fix, once a second; kept every TRACK_EVERY seconds."""
        self.ended = max(self.ended, t)
        if t - self._last_track < TRACK_EVERY:
            return
        self._last_track = t
        now = self.clock(t)
        fix = {"kismet.common.location.geopoint": [round(lon, 7), round(lat, 7)], "kismet.common.location.alt": round(alt, 1),
               "kismet.common.location.speed": round(speed * 3.6, 1), "kismet.common.location.heading": round(heading, 1),
               "kismet.common.location.fix": 3, "kismet.common.location.time_sec": int(now)}
        self.track.append((int(now), int((now % 1) * 1e6), lat, lon, "GPS", json.dumps(fix, separators=(",", ":"))))

    def observe(self, t: float, device: SimDevice, rssi: int, lat: float, lon: float, alt: float,
                speed: float, heading: float) -> Sighting:
        now = self.clock(t)
        self.ended = max(self.ended, t)
        s = self.sightings.get(device.key)
        if s is None:
            s = self.sightings[device.key] = Sighting(device, now, now, rssi, rssi, rssi, (lat, lon, alt))
        s.last = now
        s.signal = rssi
        if rssi > s.best:
            s.best, s.peak = rssi, (lat, lon, alt)
        s.worst = min(s.worst, rssi)
        s.lat_sum += lat
        s.lon_sum += lon
        s.fixes += 1
        b = s.bounds
        b[0], b[1], b[2], b[3] = min(b[0], lat), min(b[1], lon), max(b[2], lat), max(b[3], lon)
        burst = self.rng.randint(2, 25) if device.phy == "wifi" else self.rng.randint(1, 6)
        s.packets += burst
        s.bytes += burst * self.rng.randint(90, 380)
        if t - s.last_packet >= PACKET_ROW_EVERY:
            s.last_packet = t
            ts = now + self.rng.random()
            self.packets.append((
                int(ts), int((ts % 1) * 1e6), PHYNAME[device.phy], device.mac,
                "FF:FF:FF:FF:FF:FF" if device.phy == "wifi" else "00:00:00:00:00:00", device.mac,
                device.frequency, devkey(device), lat, lon, alt, speed, heading, self.rng.randint(90, 380), rssi,
                self.alfa_uuid if device.phy == "wifi" else self.bt_uuid, 127 if device.phy == "wifi" else 0,
                None, 0, None, 0.0, 0, len(self.packets) + 1, 0,
            ))
        if t - s.last_row >= WIGLE_ROW_EVERY:
            s.last_row = t
            self.rows.append(_wigle_row(s, rssi, lat, lon, alt))
        return s

    # --- output ------------------------------------------------------------------

    def write(self, log_dir: Path, modified: float | None = None) -> list[Path]:
        """Write <name>.kismet and <name>.wiglecsv atomically; returns both paths."""
        log_dir.mkdir(parents=True, exist_ok=True)
        name = self.name
        db_path, csv_path = log_dir / f"{name}.kismet", log_dir / f"{name}.wiglecsv"
        tmp_db, tmp_csv = log_dir / f".{name}.kismet.tmp", log_dir / f".{name}.wiglecsv.tmp"
        tmp_db.unlink(missing_ok=True)
        con = sqlite3.connect(tmp_db)
        try:
            con.executescript(SCHEMA)
            con.execute("insert into KISMET values (?, ?, ?)", (KISMET_RELEASE, 10, "kismetlog"))
            wifi_packets = sum(x.packets for x in self.sightings.values() if x.device.phy == "wifi")
            bt_packets = sum(x.packets for x in self.sightings.values() if x.device.phy == "bt")
            for source_uuid, name_, iface, typestring, hardware, channels, packets in (
                (self.alfa_uuid, "alfa", "alfa0", "linuxwifi", "mt76x2u",
                 ["1", "6", "11", "36", "40", "44", "48", "149", "153", "157", "161", "165"], wifi_packets),
                (self.bt_uuid, "bluetooth", "hci0", "linuxbluetooth", "hci", [], bt_packets),
            ):
                definition = f"{iface}:name={name_},type={typestring}"
                con.execute("insert into datasources values (?,?,?,?,?,?)", (
                    source_uuid, typestring, definition, name_, iface,
                    json.dumps({
                        "kismet.datasource.uuid": source_uuid, "kismet.datasource.name": name_,
                        "kismet.datasource.interface": iface, "kismet.datasource.capture_interface": iface,
                        "kismet.datasource.definition": definition, "kismet.datasource.hardware": hardware,
                        "kismet.datasource.num_packets": packets, "kismet.datasource.hopping": int(bool(channels)),
                        "kismet.datasource.hop_rate": 5.0 if channels else 0.0, "kismet.datasource.hop_channels": channels,
                        "kismet.datasource.channel": "" if channels else "FHSS", "kismet.datasource.running": 1,
                    }),
                ))
            con.executemany("insert into devices values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
                (int(s.first), int(s.last), devkey(s.device), PHYNAME[s.device.phy], s.device.mac, s.best,
                 s.bounds[0], s.bounds[1], s.bounds[2], s.bounds[3], s.lat_sum / s.fixes, s.lon_sum / s.fixes,
                 s.bytes, s.device.dev_type, json.dumps(_device_json(s), separators=(",", ":")))
                for s in self.sightings.values()
            ])
            con.executemany("insert into packets values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", self.packets)
            con.executemany("insert into snapshots values (?,?,?,?,?,?)", self.track)
            con.executemany("insert into messages values (?,?,?,?,?)", [
                (int(self.clock(self.started)), 0.0, 0.0, "INFO", "DEMO DATA: simulated by wardrive demo mode, not a real capture"),
                *self.messages,
            ])
            con.commit()
        finally:
            con.close()
        tmp_csv.write_text("\n".join([WIGLE_PREHEADER, WIGLE_HEADER, *self.rows]) + "\n")
        # Readers skip logs changed in the last ~90 s (Kismet may still be writing them).
        mtime = min(modified if modified is not None else self.ended, time.time() - UNFINISHED_WINDOW)
        for tmp, final in ((tmp_db, db_path), (tmp_csv, csv_path)):
            os.utime(tmp, (mtime, mtime))
            tmp.replace(final)
        return [db_path, csv_path]


def devkey(device: SimDevice) -> str:
    phy = 0x4202770D if device.phy == "wifi" else 0x9A6E1C4B
    return f"{phy:08X}_{int(device.mac.replace(':', ''), 16):012X}"


def _auth_as_kismet_writes_it(device: SimDevice) -> tuple[str, str]:
    if device.phy == "bt":
        return ("Misc [LE]", "BLE") if device.dev_type == "BTLE" else ("Misc [BT]", "BT")
    if device.crypt == "Open":
        return "[ESS]", "WIFI"
    return "[WPS][ESS]", "WIFI"  # Kismet 2025.09: no WPA/RSN details, WPS on every encrypted network


def _wigle_row(s: Sighting, rssi: int, lat: float, lon: float, alt: float) -> str:
    d = s.device
    auth, kind = _auth_as_kismet_writes_it(d)
    first = datetime.fromtimestamp(s.first, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    channel = d.channel if d.phy == "wifi" else "0"
    return f"{d.mac},{d.name if d.phy == 'wifi' else d.name.replace(',', ' ')},{auth},{first},{channel},{rssi},{lat:.6f},{lon:.6f},{alt:.1f},0,{kind}"


def _geo(lat: float, lon: float, alt: float = 0.0) -> dict:
    return {"kismet.common.location.geopoint": [round(lon, 7), round(lat, 7)], "kismet.common.location.alt": round(alt, 1),
            "kismet.common.location.fix": 3}


def _device_json(s: Sighting) -> dict:
    d = s.device
    base = {
        "kismet.device.base.key": devkey(d),
        "kismet.device.base.macaddr": d.mac,
        "kismet.device.base.phyname": PHYNAME[d.phy],
        "kismet.device.base.name": d.name,
        "kismet.device.base.commonname": d.name or d.mac,
        "kismet.device.base.type": d.dev_type,
        "kismet.device.base.crypt": d.crypt,
        "kismet.device.base.channel": d.channel,
        "kismet.device.base.frequency": d.frequency,
        "kismet.device.base.manuf": d.manuf,
        "kismet.device.base.first_time": int(s.first),
        "kismet.device.base.last_time": int(s.last),
        "kismet.device.base.packets.total": s.packets,
        "kismet.device.base.datasize": s.bytes,
        "kismet.device.base.signal": {
            "kismet.common.signal.type": "dbm",
            "kismet.common.signal.last_signal": s.signal,
            "kismet.common.signal.min_signal": s.worst,
            "kismet.common.signal.max_signal": s.best,
            "kismet.common.signal.peak_loc": _geo(*s.peak),
        },
        "kismet.device.base.location": {
            "kismet.common.location.avg_loc": _geo(s.lat_sum / s.fixes, s.lon_sum / s.fixes, s.peak[2]),
            "kismet.common.location.min_loc": _geo(s.bounds[0], s.bounds[1]),
            "kismet.common.location.max_loc": _geo(s.bounds[2], s.bounds[3]),
        },
    }
    if d.phy == "wifi":
        record = {
            "dot11.advertisedssid.ssid": d.name,
            "dot11.advertisedssid.ssidlen": len(d.name.encode()),
            "dot11.advertisedssid.crypt_string": d.crypt,
            "dot11.advertisedssid.channel": d.channel,
            "dot11.advertisedssid.first_time": int(s.first),
            "dot11.advertisedssid.last_time": int(s.last),
            "dot11.advertisedssid.beacons_sec": 10,
        }
        if d.wps:
            record["dot11.advertisedssid.wps_state"] = 2  # configured
        base["dot11.device"] = {
            "dot11.device.last_beaconed_ssid_record": record,
            "dot11.device.num_advertised_ssids": 1,
            "dot11.device.typeset": 1,  # AP
        }
    return base

