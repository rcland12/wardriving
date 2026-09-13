"""Demo backend: the real app, with GPS and capture simulated instead of gpsd and Kismet.

Parked at the start point until START, then drives the map's roads while the simulated
radios fill the network list. STOP writes the session to the demo logs directory in
Kismet's formats, so SESSIONS, the map and UPLOAD all work on it like on a real capture.
"""

from __future__ import annotations

import logging
import math
import random
import threading
import time

from ..backend import _sudo_systemctl
from ..mapdata import find_map
from ..sessions import SessionLibrary
from ..state import Capture, Device, Source, State, Summary, classify_crypt
from ..sysinfo import SysInfoPoller
from ..uploads import UploadManager
from . import simulate
from .files import SessionRecorder
from .world import MapIndex, Scanner, World, make_driver

log = logging.getLogger(__name__)

TICK = 0.5  # s
GPS_SEARCH_S = 6.0  # simulated time to first fix after the UI starts
SATELLITES = [5, 13, 15, 18, 23, 24, 29, 10, 26, 2, 7, 16, 20, 31]


class DemoBackend:
    mock = False
    demo = True

    def __init__(self, cfg, state: State, *, real_power: bool, seed: int | None = None):
        """real_power: REBOOT and SHUTDOWN act on the machine (the Pi) instead of only logging."""
        self.cfg, self.state, self.real_power = cfg, state, real_power
        self.rng = random.Random(seed)
        self.uploads = UploadManager(cfg, state, demo=True)
        # A demo capture is only written when it stops, so no log file is ever half-written.
        self.sessions = SessionLibrary(cfg.log_dir, cfg.state_path / "sessions")
        self._lock = threading.RLock()
        self._recorder: SessionRecorder | None = None
        self._booted = time.monotonic()
        self._second = 0.0
        self._alt = 96.0
        mapfile = find_map(cfg.map.dir)  # its own handle: the UI thread uses another
        self._index = MapIndex(mapfile) if mapfile else None
        self.world = World(self._index, seed=1)
        self.driver = make_driver(self._index, cfg.demo.lat, cfg.demo.lon, self.rng)
        self.scanner = Scanner(self.world, self.rng)

    # --- lifecycle ---------------------------------------------------------------

    def start(self) -> None:
        st = self.state
        SysInfoPoller(st, self.cfg.log_dir, clock=lambda: "GPS").start()
        st.gps.gpsd_up = True
        st.log("info", "Demo mode: simulated GPS and capture. MENU → DEMO to leave.")
        threading.Thread(target=self._run, name="demo", daemon=True).start()

    def prefill(self, seconds: float = 1800) -> None:
        """Jump into a running capture `seconds` long (for screenshots)."""
        st = self.state
        self._booted -= GPS_SEARCH_S
        st.reset_session()
        start = time.time() - seconds
        with self._lock:
            self._recorder = SessionRecorder(start, seed=self.rng.randrange(2**31))
            st.session_start = time.monotonic() - seconds
            st.capture = Capture.RUNNING
            st.distance_m = simulate(self._recorder, self.driver, self.scanner, start, seconds)
            for s in self._recorder.sightings.values():
                self._publish(s, new=False)
        self._update_gps(time.monotonic())
        self._update_sources()
        for level, text in [
            ("info", "Starting capture (simulated)"), ("good", "Capture running"),
            ("info", "Data source 'alfa' launched successfully"), ("good", "GPS fix acquired (3D)"),
            ("warn", "bluetooth: Bluetooth interface hci0 is soft blocked by rfkill, retrying"),
            ("good", "bluetooth: recovered"),
        ]:
            st.log(level, text)

    # --- commands from the UI ------------------------------------------------------

    def start_capture(self) -> None:
        st = self.state
        if st.capture not in (Capture.IDLE, Capture.ERROR):
            return
        st.capture = Capture.STARTING

        def run():
            st.log("info", "Starting capture (simulated)")
            time.sleep(1.2)
            with self._lock:
                st.reset_session()
                self._recorder = SessionRecorder(time.time(), seed=self.rng.randrange(2**31))
                self._recorder.message(time.time(), "Data source 'alfa' launched successfully")
                self._recorder.message(time.time(), "Data source 'bluetooth' launched successfully")
                st.session_start = time.monotonic()
                st.capture = Capture.RUNNING
            st.log("good", "Capture running")

        threading.Thread(target=run, name="demo-start", daemon=True).start()

    def stop_capture(self) -> None:
        st = self.state
        if st.capture not in (Capture.RUNNING, Capture.ERROR):
            return
        duration = st.session_seconds()
        st.capture = Capture.STOPPING

        def run():
            with self._lock:
                recorder, self._recorder = self._recorder, None
            files = []
            if recorder is not None:
                try:
                    files = [p.name for p in recorder.write(self.sessions.log_dir)]
                except OSError as exc:
                    st.log("error", f"Could not save demo session: {exc}")
            c = st.counts()
            st.summary = Summary(duration, c["wifi"], c["bt"], c["OPEN"], files)
            st.capture = Capture.IDLE
            st.log("good", f"Capture stopped: {c['wifi']} Wi-Fi, {c['bt']} BT")

        threading.Thread(target=run, name="demo-stop", daemon=True).start()

    def poweroff(self) -> None:
        self._power("poweroff")

    def reboot(self) -> None:
        self._power("reboot")

    def _power(self, action: str) -> None:
        if not self.real_power:
            self.state.log("warn", f"(demo) {action} requested")
            return

        def run():
            if self.state.capture == Capture.RUNNING:
                self.stop_capture()
                time.sleep(2)
            self.state.log("warn", f"System {action}")
            ok, err = _sudo_systemctl(action, timeout=10)
            if not ok:
                self.state.log("error", f"{action} failed: {err}")

        threading.Thread(target=run, name=action, daemon=True).start()

    # --- simulation ------------------------------------------------------------------

    def _run(self) -> None:
        while True:
            try:
                self._tick(time.monotonic())
            except Exception:  # keep the simulation alive whatever happens
                log.exception("demo simulation step failed")
            time.sleep(TICK)

    def _tick(self, now: float) -> None:
        st = self.state
        with self._lock:
            if st.capture == Capture.RUNNING and self._recorder is not None:
                st.distance_m += self.driver.step(TICK)
                self._second += TICK
                if self._second >= 1.0:
                    self._second -= 1.0
                    self._scan(time.time())
                self._update_sources()
            else:
                self.driver.speed = 0.0
        self._update_gps(now)

    def _scan(self, t: float) -> None:
        d = self.driver
        self._alt += (self.rng.random() - 0.5) * 0.4
        self._recorder.position(t, d.lat, d.lon, self._alt, d.speed, d.heading)
        for device, rssi in self.scanner.scan(t, d.lat, d.lon, d.speed):
            is_new = device.key not in self._recorder.sightings
            s = self._recorder.observe(t, device, rssi, d.lat, d.lon, self._alt, d.speed, d.heading)
            self._publish(s, new=is_new)

    def _publish(self, s, new: bool) -> None:
        dev, st = s.device, self.state
        with st.lock:
            old = st.devices.get(dev.key)
            st.devices[dev.key] = Device(
                key=dev.key, mac=dev.mac, phy=dev.phy, name=dev.name, channel=dev.channel,
                crypt=classify_crypt(dev.crypt, dev.phy), crypt_raw=dev.crypt, manuf=dev.manuf,
                signal=s.signal, first_seen=s.first, last_seen=s.last, lat=s.peak[0], lon=s.peak[1],
                new_until=time.monotonic() + 3 if new else (old.new_until if old else 0.0),
            )
            st.version += 1

    def _update_gps(self, now: float) -> None:
        g, d = self.state.gps, self.driver
        g.last_report = now
        searching = now - self._booted < GPS_SEARCH_S
        wobble = math.sin(now / 7)
        g.satellites = [
            (prn, max(10.0, (22 if searching else 46) - i * 2.4 + 2 * math.sin(now / 5 + i)), not searching and i < 9)
            for i, prn in enumerate(SATELLITES[: 6 if searching else len(SATELLITES)])
        ]
        g.sats_seen = len(g.satellites)
        g.sats_used = sum(1 for s in g.satellites if s[2])
        if searching:
            g.mode, g.hdop = 1, 0.0
            if not g.searching_since:
                g.searching_since = self._booted
            return
        g.mode, g.hdop = 3, round(0.9 + 0.1 * wobble, 2)
        g.lat, g.lon = d.lat, d.lon
        g.alt, g.speed, g.track = self._alt, d.speed, d.heading
        g.time = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())

    def _update_sources(self) -> None:
        st = self.state
        packets = sum(s.packets for s in self._recorder.sightings.values()) if self._recorder else 0
        wifi_packets = int(packets * 0.93)
        channel = str(self.rng.choice([1, 6, 11, 36, 149]))
        st.sources = [
            Source("alfa", "alfa0", True, "", channel, True, wifi_packets),
            Source("bluetooth", "hci0", True, "", "", False, packets - wifi_packets),
        ]
        st.packets_per_sec = max(0.0, self.rng.gauss(55 if self.driver.speed > 1 else 30, 8))
        st.kismet_rss_kb = 52_000 + len(st.devices) * 3
