"""gpsd JSON client. Runs in a thread and keeps State.gps current."""

from __future__ import annotations

import json
import logging
import math
import socket
import threading
import time

from .state import Capture, State

log = logging.getLogger(__name__)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class GpsClient(threading.Thread):
    def __init__(self, state: State, host: str, port: int):
        super().__init__(name="gpsd", daemon=True)
        self.state, self.host, self.port = state, host, port
        self._last_pos: tuple[float, float] | None = None

    def run(self) -> None:
        while True:
            try:
                with socket.create_connection((self.host, self.port), timeout=5) as sock:
                    sock.sendall(b'?WATCH={"enable":true,"json":true}\n')
                    sock.settimeout(10)
                    self.state.gps.gpsd_up = True
                    buf = b""
                    while True:
                        chunk = sock.recv(4096)
                        if not chunk:
                            raise ConnectionError("gpsd closed connection")
                        buf += chunk
                        *lines, buf = buf.split(b"\n")
                        for line in lines:
                            self._on_message(line)
            except (OSError, ConnectionError) as exc:
                if self.state.gps.gpsd_up:
                    self.state.log("warn", f"gpsd connection lost ({exc})")
                self.state.gps.gpsd_up = False
                time.sleep(3)

    def _on_message(self, line: bytes) -> None:
        try:
            msg = json.loads(line)
        except ValueError:
            return
        g = self.state.gps
        cls = msg.get("class")
        if cls == "TPV":
            had_fix = g.has_fix
            g.last_report = time.monotonic()
            g.mode = int(msg.get("mode", 0))
            if g.mode >= 2 and "lat" in msg and "lon" in msg:
                g.lat, g.lon = msg["lat"], msg["lon"]
                g.alt = msg.get("altHAE", msg.get("alt", g.alt))
                g.speed = msg.get("speed", 0.0)
                g.track = msg.get("track", g.track)
                self._accumulate_distance(g.lat, g.lon, g.speed)
            g.time = msg.get("time", g.time)
            if g.mode >= 2:
                g.searching_since = 0.0
            elif not g.searching_since:
                g.searching_since = time.monotonic()
            if g.has_fix and not had_fix:
                self.state.log("good", f"GPS fix acquired ({g.mode}D)")
            elif had_fix and not g.has_fix:
                self.state.log("warn", "GPS fix lost")
        elif cls == "SKY":
            sats = msg.get("satellites") or []
            g.sats_seen = int(msg.get("nSat", len(sats)))
            g.sats_used = int(msg.get("uSat", sum(1 for s in sats if s.get("used"))))
            g.satellites = sorted(
                ((int(s.get("PRN", 0)), float(s.get("ss", 0) or 0), bool(s.get("used"))) for s in sats),
                key=lambda s: (-s[1], s[0]),
            )
            if "hdop" in msg:
                g.hdop = msg["hdop"]

    def _accumulate_distance(self, lat: float, lon: float, speed: float) -> None:
        if self.state.capture != Capture.RUNNING:
            self._last_pos = None
            return
        if self._last_pos and speed > 1.0:  # ignore jitter while parked
            self.state.distance_m += haversine_m(*self._last_pos, lat, lon)
        self._last_pos = (lat, lon)
