"""Minimal Kismet REST client for the handful of endpoints the UI needs."""

from __future__ import annotations

import json
from pathlib import Path

import requests

from .state import Device, Source, classify_crypt

_B = "kismet.device.base."
DEVICE_FIELDS = [
    _B + "key",
    _B + "macaddr",
    _B + "phyname",
    _B + "name",
    _B + "channel",
    _B + "crypt",
    _B + "manuf",
    _B + "first_time",
    _B + "last_time",
    [_B + "signal/kismet.common.signal.last_signal", "signal"],
    ["dot11.device/dot11.device.last_beaconed_ssid_record/dot11.advertisedssid.ssid", "ssid"],
    [_B + "location/kismet.common.location.last/kismet.common.location.geopoint", "geopoint"],
]

PHY = {"IEEE802.11": "wifi", "Bluetooth": "bt"}


class KismetError(Exception):
    pass


def read_auth(path: str) -> tuple[str, str] | None:
    """Parse httpd_username/httpd_password from Kismet's per-user auth file."""
    try:
        text = Path(path).expanduser().read_text()
    except OSError:
        return None
    values = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
    user, password = values.get("httpd_username"), values.get("httpd_password")
    return (user, password) if user and password else None


class KismetClient:
    def __init__(self, url: str, auth_file: str, timeout: float = 5.0):
        self.url = url.rstrip("/")
        self.auth_file = auth_file
        self.timeout = timeout
        self.session = requests.Session()

    def _request(self, method: str, path: str, **kwargs):
        if self.session.auth is None:
            self.session.auth = read_auth(self.auth_file)
        try:
            resp = self.session.request(method, self.url + path, timeout=self.timeout, **kwargs)
        except requests.RequestException as exc:
            raise KismetError(f"kismet unreachable: {exc.__class__.__name__}") from exc
        if resp.status_code == 401:
            self.session.auth = None  # re-read credentials next time
            raise KismetError("kismet rejected credentials")
        if not resp.ok:
            raise KismetError(f"kismet {path}: HTTP {resp.status_code}")
        return resp.json()

    def status(self) -> dict:
        return self._request("GET", "/system/status.json")

    def sources(self) -> list[Source]:
        out = []
        for s in self._request("GET", "/datasource/all_sources.json"):
            k = "kismet.datasource."
            out.append(
                Source(
                    name=s.get(k + "name", "?"),
                    interface=s.get(k + "interface", ""),
                    running=bool(s.get(k + "running")),
                    error=s.get(k + "error_reason", "") if s.get(k + "error") else "",
                    channel=str(s.get(k + "channel", "")),
                    hopping=bool(s.get(k + "hopping")),
                    packets=int(s.get(k + "num_packets", 0)),
                )
            )
        return out

    def devices_since(self, since: int) -> list[Device]:
        """Devices active since `since` (epoch seconds, or negative = seconds ago)."""
        payload = {"json": json.dumps({"fields": DEVICE_FIELDS})}
        rows = self._request("POST", f"/devices/last-time/{since}/devices.json", data=payload)
        devices = []
        for r in rows:
            phy = PHY.get(r.get(_B + "phyname"))
            if phy is None:
                continue
            crypt_raw = r.get(_B + "crypt") or ""
            ssid = r.get("ssid")
            name = ssid if isinstance(ssid, str) else ""
            if phy == "bt":
                name = r.get(_B + "name") or ""
            geo = r.get("geopoint")
            lon, lat = geo if isinstance(geo, list) and len(geo) == 2 else (0.0, 0.0)
            signal = r.get("signal")
            devices.append(
                Device(
                    key=r[_B + "key"],
                    mac=r.get(_B + "macaddr", ""),
                    phy=phy,
                    name=name,
                    channel=str(r.get(_B + "channel", "")),
                    crypt=classify_crypt(crypt_raw, phy),
                    crypt_raw=crypt_raw,
                    manuf=r.get(_B + "manuf", "") or "",
                    signal=signal if isinstance(signal, int) else 0,
                    first_seen=float(r.get(_B + "first_time", 0)),
                    last_seen=float(r.get(_B + "last_time", 0)),
                    lat=lat,
                    lon=lon,
                )
            )
        return devices

    def messages_since(self, since: int) -> list[dict]:
        data = self._request("GET", f"/messagebus/last-time/{since}/messages.json")
        return data.get("kismet.messagebus.list", []) if isinstance(data, dict) else data
