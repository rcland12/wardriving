"""Simulated backend for developing the UI away from the Pi."""

from __future__ import annotations

import math
import random
import threading
import time

from .state import Capture, Device, Source, State, Summary, classify_crypt
from .uploads import LogSession

SSIDS = [
    "ATT-5G-8812", "xfinitywifi", "NETGEAR47", "CoxWiFi", "Starbucks WiFi", "DIRECT-roku-551",
    "HP-Print-3C-LaserJet", "TP-Link_A4F2", "Linksys00321", "MySpectrumWiFi7a-5G", "FBI Surveillance Van",
    "Verizon_9QXK4T", "eero", "Google Nest", "", "", "Pretty Fly for a WiFi", "Tesla Service",
    "GUEST", "Chick-fil-A WiFi", "attwifi", "Bill Wi the Science Fi", "", "SETUP-58B2",
]
CRYPTS = ["WPA2 WPA2-PSK AES-CCMP"] * 6 + ["WPA3 WPA3-SAE AES-CCMP"] * 2 + ["Open"] * 2 + ["WEP"]
BT_NAMES = ["", "", "", "JBL Flip 5", "Galaxy Buds2", "Tile", "[TV] Samsung 7 Series"]
VENDORS = ["Netgear", "TP-Link", "Arris", "Ubiquiti", "Apple", "Samsung Electronics", "Unknown"]
CHANNELS = ["1", "6", "11", "36", "40", "44", "149", "153", "157", "161"]


def _mac() -> str:
    return ":".join(f"{random.randrange(256):02X}" for _ in range(6))


class MockUploads:
    def __init__(self, state: State):
        self.state = state
        self._done = {("wardrive-20260910-22-15-03-1", "wigle")}
        self._sessions = [
            LogSession("wardrive-20260912-01-02-11-1", wigle_rows=1843, size=18_400_000),
            LogSession("wardrive-20260911-23-40-52-1", wigle_rows=652, size=6_100_000),
            LogSession("wardrive-20260910-22-15-03-1", wigle_rows=2210, size=24_800_000),
        ]

    def sessions(self):
        return self._sessions

    def uploaded(self, name, target):
        return (name, target) in self._done

    def enabled(self, target):
        return True

    def label(self, target):
        return "WiGLE" if target == "wigle" else "Home server"

    def pending(self, target):
        return [s for s in self._sessions if (s.name, target) not in self._done]

    def upload(self, target):
        def run():
            self.state.upload_busy = True
            for i, s in enumerate(self.pending(target), 1):
                self.state.upload_status = f"{self.label(target)}: {i} {s.name}"
                time.sleep(1.5)
                self._done.add((s.name, target))
            self.state.upload_status = f"{self.label(target)}: done"
            self.state.upload_busy = False

        threading.Thread(target=run, daemon=True).start()


class MockBackend:
    mock = True

    def __init__(self, cfg, state: State):
        self.cfg, self.state = cfg, state
        self.uploads = MockUploads(state)

    def start(self) -> None:
        st = self.state
        st.gps.gpsd_up = True
        st.sys.cpu_temp, st.sys.disk_free_bytes = 52.0, 104 * 1024**3
        st.sys.ip, st.sys.online, st.sys.clock_source = "192.168.1.100", True, "GPS"
        st.log("info", "Mock backend started")
        threading.Thread(target=self._sim, name="mock", daemon=True).start()

    def prefill(self, n: int = 60) -> None:
        """Jump straight to a running session with some devices (for screenshots)."""
        st = self.state
        st.reset_session()
        st.session_start = time.monotonic() - 1834
        st.capture = Capture.RUNNING
        st.distance_m = 14_250
        for _ in range(n):
            self._add_device(time.time() - random.uniform(0, 1800))
        self._update_gps(time.monotonic())
        self._update_sources()
        for level, text in [
            ("info", "Starting capture"), ("good", "Capture running"),
            ("info", "Data source 'alfa' launched successfully"), ("good", "GPS fix acquired (3D)"),
            ("warn", "bluetooth: Bluetooth interface hci0 is soft blocked by rfkill, retrying"),
            ("good", "bluetooth: recovered"),
        ]:
            st.log(level, text)

    def start_capture(self) -> None:
        def run():
            st = self.state
            st.capture = Capture.STARTING
            st.reset_session()
            st.log("info", "Starting capture")
            time.sleep(1.5)
            st.session_start = time.monotonic()
            st.capture = Capture.RUNNING
            st.log("good", "Capture running")

        threading.Thread(target=run, daemon=True).start()

    def stop_capture(self) -> None:
        def run():
            st = self.state
            dur = st.session_seconds()
            st.capture = Capture.STOPPING
            time.sleep(1)
            c = st.counts()
            st.summary = Summary(dur, c["wifi"], c["bt"], c["OPEN"], ["wardrive-mock-1.kismet", "wardrive-mock-1.wiglecsv"])
            st.capture = Capture.IDLE
            st.log("good", f"Capture stopped: {c['wifi']} Wi-Fi, {c['bt']} BT")

        threading.Thread(target=run, daemon=True).start()

    def poweroff(self) -> None:
        self.state.log("warn", "(mock) poweroff requested")

    def reboot(self) -> None:
        self.state.log("warn", "(mock) reboot requested")

    # --- simulation ------------------------------------------------------------

    def _add_device(self, first_seen: float) -> None:
        st = self.state
        bt = random.random() < 0.25
        phy = "bt" if bt else "wifi"
        crypt_raw = "" if bt else random.choice(CRYPTS)
        d = Device(
            key=_mac(), mac=_mac(), phy=phy,
            name=random.choice(BT_NAMES if bt else SSIDS),
            channel="FHSS" if bt else random.choice(CHANNELS),
            crypt=classify_crypt(crypt_raw, phy), crypt_raw=crypt_raw,
            manuf=random.choice(VENDORS), signal=random.randint(-92, -38),
            first_seen=first_seen, last_seen=time.time(),
            lat=st.gps.lat, lon=st.gps.lon,
        )
        with st.lock:
            st.devices[d.key] = d
            st.version += 1

    def _update_gps(self, t: float) -> None:
        g = self.state.gps
        g.last_report = time.monotonic()
        g.mode, g.sats_used, g.sats_seen, g.hdop = 3, 9, 14, 0.9
        g.satellites = [(p, max(12.0, 47 - i * 2.6), i < 9) for i, p in enumerate([5, 13, 15, 18, 23, 24, 29, 10, 26, 2, 7, 16, 20, 31])]
        g.lat = 39.8283 + 0.01 * math.sin(t / 120)
        g.lon = -98.5795 + 0.01 * math.cos(t / 120)
        g.alt, g.speed, g.track = 96.0, 15.6, (t * 3) % 360
        g.time = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())

    def _update_sources(self) -> None:
        pk = int(self.state.session_seconds() * 90)
        self.state.sources = [
            Source("alfa", "alfa0", True, "", "", True, pk),
            Source("bluetooth", "hci0", True, "", "", False, pk // 20),
        ]
        self.state.packets_per_sec = random.uniform(60, 120)
        self.state.kismet_rss_kb = 48_000

    def _sim(self) -> None:
        while True:
            now = time.monotonic()
            self._update_gps(now)
            st = self.state
            if st.capture == Capture.RUNNING:
                if random.random() < 0.5:
                    self._add_device(time.time())
                    with st.lock:
                        list(st.devices.values())[-1].new_until = now + 3
                with st.lock:
                    for d in random.sample(list(st.devices.values()), min(5, len(st.devices))):
                        d.signal = max(-95, min(-30, d.signal + random.randint(-4, 4)))
                self._update_sources()
                st.distance_m += st.gps.speed * 0.5
            time.sleep(0.5)
