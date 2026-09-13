"""Shared application state. Background threads write, the UI thread reads."""

from __future__ import annotations

import enum
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass, field


class Capture(enum.Enum):
    IDLE = "idle"
    WAITING = "waiting"  # START pressed; holding until the system clock is set (GPS or NTP)
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


def classify_crypt(raw: str, phy: str) -> str:
    """Collapse Kismet's crypt string ('WPA2 WPA2-PSK AES-CCMP') into one label."""
    if phy == "bt":
        return "BT"
    if "WPA3" in raw:
        return "WPA3"
    if "WPA2" in raw:
        return "WPA2"
    if "WPA" in raw:
        return "WPA"
    if "WEP" in raw:
        return "WEP"
    return "OPEN"


@dataclass
class Device:
    key: str
    mac: str
    phy: str  # "wifi" | "bt"
    name: str  # SSID or BT name; "" if hidden/unknown
    channel: str
    crypt: str  # classified label
    crypt_raw: str
    manuf: str
    signal: int  # dBm, 0 = unknown
    first_seen: float
    last_seen: float
    lat: float = 0.0
    lon: float = 0.0
    new_until: float = 0.0  # monotonic time until which the row is highlighted

    @property
    def label(self) -> str:
        return self.name or ("<hidden>" if self.phy == "wifi" else self.mac)


@dataclass
class GpsFix:
    gpsd_up: bool = False
    last_report: float = 0.0  # monotonic time of last TPV
    mode: int = 0  # 0/1 no fix, 2 = 2D, 3 = 3D
    lat: float = 0.0
    lon: float = 0.0
    alt: float = 0.0  # m
    speed: float = 0.0  # m/s
    track: float = 0.0  # degrees
    sats_used: int = 0
    sats_seen: int = 0
    hdop: float = 0.0
    time: str = ""
    satellites: list[tuple[int, float, bool]] = field(default_factory=list)  # (PRN, SNR dB-Hz, used)
    searching_since: float = 0.0  # monotonic time the receiver started reporting without a fix

    @property
    def device_present(self) -> bool:
        return self.gpsd_up and time.monotonic() - self.last_report < 5

    @property
    def time_valid(self) -> bool:
        """False while the receiver still reports its 1980 epoch (cold start, no almanac)."""
        return bool(self.time) and not self.time.startswith(("1980", "1970"))

    @property
    def has_fix(self) -> bool:
        return self.device_present and self.mode >= 2


@dataclass
class Source:
    name: str
    interface: str
    running: bool
    error: str
    channel: str
    hopping: bool
    packets: int


@dataclass
class SysInfo:
    cpu_temp: float = 0.0
    undervolt_now: bool = False
    throttled_now: bool = False
    undervolt_since_boot: bool = False
    disk_free_bytes: int = 0
    online: bool = False
    ip: str = ""
    clock_source: str = ""


@dataclass
class Summary:
    duration: float
    wifi: int
    bt: int
    open: int
    files: list[str]


@dataclass
class Event:
    when: float  # wall clock
    level: str  # "info" | "good" | "warn" | "error"
    text: str


@dataclass
class State:
    capture: Capture = Capture.IDLE
    capture_error: str = ""
    session_start: float = 0.0  # monotonic
    devices: dict[str, Device] = field(default_factory=dict)
    sources: list[Source] = field(default_factory=list)
    gps: GpsFix = field(default_factory=GpsFix)
    sys: SysInfo = field(default_factory=SysInfo)
    packets_per_sec: float = 0.0
    kismet_rss_kb: int = 0
    distance_m: float = 0.0
    summary: Summary | None = None
    upload_status: str = ""
    upload_busy: bool = False
    events: deque[Event] = field(default_factory=lambda: deque(maxlen=200))
    version: int = 0  # bumped whenever devices change
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def log(self, level: str, text: str) -> None:
        with self.lock:
            self.events.append(Event(time.time(), level, text))

    def session_seconds(self) -> float:
        if self.capture in (Capture.RUNNING, Capture.STOPPING) and self.session_start:
            return time.monotonic() - self.session_start
        return 0.0

    def reset_session(self) -> None:
        with self.lock:
            self.devices.clear()
            self.sources = []
            self.packets_per_sec = 0.0
            self.distance_m = 0.0
            self.summary = None
            self.session_start = time.monotonic()
            self.version += 1

    def counts(self) -> Counter:
        """Counter with keys 'wifi', 'bt' and each crypt label."""
        with self.lock:
            c = Counter()
            for d in self.devices.values():
                c[d.phy] += 1
                if d.phy == "wifi":
                    c[d.crypt] += 1
            return c

    def device_list(self, phy: str | None, sort: str) -> list[Device]:
        with self.lock:
            items = [d for d in self.devices.values() if phy is None or d.phy == phy]
        if sort == "signal":
            items.sort(key=lambda d: (d.signal == 0, -d.signal))
        else:  # "new": most recently discovered first
            items.sort(key=lambda d: -d.first_seen)
        return items
