"""Real backend: controls the Kismet service and mirrors its data into State."""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from pathlib import Path

from .config import Config
from .gps import GpsClient
from .kismet import KismetClient, KismetError
from .state import Capture, State, Summary
from .sysinfo import SYNCED_CLOCK_SOURCES, SysInfoPoller, clock_source
from .uploads import UploadManager

log = logging.getLogger(__name__)

# Kismet info messages worth surfacing in the LOG view; warnings/errors always show.
_MSG_INFO_KEEP = ("source", "gps", "log file", "channel")


def _sudo_systemctl(*args: str, timeout: float) -> tuple[bool, str]:
    # Must match the exact command lines allowed in system/sudoers-wardrive.
    cmd = ["sudo", "-n", "/usr/bin/systemctl", *args]
    action = args[0]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"systemctl {action} timed out"
    except OSError as exc:
        return False, str(exc)
    return res.returncode == 0, (res.stderr or res.stdout).strip()


def _unit_active(unit: str) -> bool:
    res = subprocess.run(["systemctl", "is-active", "--quiet", unit], timeout=5)
    return res.returncode == 0


class Backend:
    mock = False

    def __init__(self, cfg: Config, state: State):
        self.cfg, self.state = cfg, state
        self.kismet = KismetClient(cfg.kismet.url, cfg.kismet.auth_file)
        self.uploads = UploadManager(cfg, state)
        self._busy = threading.Lock()  # serializes start/stop
        self._cancel_wait = threading.Event()
        self._last_packets: tuple[float, int] | None = None
        self._last_msg_time = 0
        self._seen_source_errors: set[str] = set()

    # --- lifecycle -------------------------------------------------------------

    def start(self) -> None:
        GpsClient(self.state, self.cfg.gps.host, self.cfg.gps.port).start()
        SysInfoPoller(self.state, self.cfg.log_dir).start()
        threading.Thread(target=self._poll_loop, name="kismet", daemon=True).start()

    # --- commands from the UI --------------------------------------------------

    def start_capture(self) -> None:
        if self.state.capture not in (Capture.IDLE, Capture.ERROR):
            return
        self.state.capture = Capture.STARTING  # set now, so a double tap can't start twice
        self._cancel_wait.clear()
        threading.Thread(target=self._do_start, name="capture-start", daemon=True).start()

    def stop_capture(self) -> None:
        if self.state.capture == Capture.WAITING:
            self._cancel_wait.set()  # _do_start notices and backs out
            return
        if self.state.capture not in (Capture.RUNNING, Capture.STARTING, Capture.ERROR):
            return
        threading.Thread(target=self._do_stop, name="capture-stop", daemon=True).start()

    def poweroff(self) -> None:
        self._shutdown("poweroff")

    def reboot(self) -> None:
        self._shutdown("reboot")

    # --- internals -------------------------------------------------------------

    def _wait_for_clock(self) -> bool:
        """Hold until chrony reports a synced clock. False if the user cancelled."""
        st = self.state
        if not self.cfg.capture.wait_for_clock or clock_source() in SYNCED_CLOCK_SOURCES:
            return True
        st.capture = Capture.WAITING
        st.log("warn", "Clock not set yet: waiting for GPS time before capturing")
        while not self._cancel_wait.wait(1.0):
            source = clock_source()
            st.sys.clock_source = source
            if source in SYNCED_CLOCK_SOURCES:
                st.log("good", f"Clock set from {source}")
                st.capture = Capture.STARTING
                return True
        st.capture = Capture.IDLE
        st.log("info", "Capture cancelled while waiting for GPS time")
        return False

    def _do_start(self) -> None:
        with self._busy:
            st = self.state
            if not self._wait_for_clock():
                return
            st.capture, st.capture_error = Capture.STARTING, ""
            st.reset_session()
            self._last_packets = None
            self._seen_source_errors.clear()
            st.log("info", "Starting capture")
            ok, err = _sudo_systemctl("start", self.cfg.kismet.service, timeout=30)
            if not ok:
                st.capture, st.capture_error = Capture.ERROR, err or "failed to start Kismet"
                st.log("error", f"Kismet failed to start: {st.capture_error}")
                return
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                try:
                    status = self.kismet.status()
                    self._last_msg_time = int(status.get("kismet.system.timestamp.sec", 0))
                    break
                except KismetError:
                    time.sleep(1)
            else:
                st.capture, st.capture_error = Capture.ERROR, "Kismet API did not come up"
                st.log("error", st.capture_error)
                return
            st.session_start = time.monotonic()
            st.capture = Capture.RUNNING
            st.log("good", "Capture running")

    def _do_stop(self) -> None:
        with self._busy:
            st = self.state
            duration = st.session_seconds()
            started_wall = time.time() - duration
            st.capture = Capture.STOPPING
            st.log("info", "Stopping capture")
            ok, err = _sudo_systemctl("stop", self.cfg.kismet.service, timeout=45)
            if not ok:
                st.log("error", f"Kismet stop failed: {err}")
            c = st.counts()
            st.summary = Summary(
                duration=duration,
                wifi=c["wifi"],
                bt=c["bt"],
                open=c["OPEN"],
                files=[p.name for p in self._logs_modified_since(started_wall)],
            )
            st.capture = Capture.IDLE
            st.log("good", f"Capture stopped: {c['wifi']} Wi-Fi, {c['bt']} BT")

    def _logs_modified_since(self, cutoff: float) -> list[Path]:
        # Earlier sessions' files stopped changing before this session started. The
        # .kismet db is written continuously; siblings (e.g. a .wiglecsv with no GPS
        # rows) may not be, so match them by file stem.
        try:
            files = [p for p in Path(self.cfg.log_dir).iterdir() if p.is_file()]
            stems = {p.stem for p in files if p.suffix == ".kismet" and p.stat().st_mtime >= cutoff}
            return sorted(p for p in files if p.stem in stems)
        except OSError:
            return []

    def _shutdown(self, action: str) -> None:
        def run():
            if self.state.capture == Capture.WAITING:
                self._cancel_wait.set()
                with self._busy:  # let _do_start back out before powering off
                    pass
            if self.state.capture in (Capture.RUNNING, Capture.STARTING):
                self._do_stop()
            self.state.log("warn", f"System {action}")
            ok, err = _sudo_systemctl(action, timeout=10)
            if not ok:
                self.state.log("error", f"{action} failed: {err}")

        threading.Thread(target=run, name=action, daemon=True).start()

    def _poll_loop(self) -> None:
        # If the UI restarted while Kismet was running, adopt the running session.
        if _unit_active(self.cfg.kismet.service):
            try:
                status = self.kismet.status()
                started = status.get("kismet.system.timestamp.start_sec", 0)
                now = status.get("kismet.system.timestamp.sec", 0)
                self.state.session_start = time.monotonic() - max(0, now - started)
                self._last_msg_time = now
                self.state.capture = Capture.RUNNING
                self.state.log("info", "Resumed running capture")
                self._poll_devices(since=0)
            except Exception as exc:
                log.warning("could not adopt running capture: %s", exc)

        tick = 0
        while True:
            time.sleep(self.cfg.kismet.poll_seconds)
            st = self.state
            if st.capture != Capture.RUNNING:
                continue
            try:
                self._poll_devices(since=-int(self.cfg.kismet.poll_seconds * 2 + 3))
                if tick % 2 == 0:
                    self._poll_sources()
                    self._poll_messages()
                tick += 1
            except KismetError as exc:
                if st.capture == Capture.RUNNING and not _unit_active(self.cfg.kismet.service):
                    st.capture, st.capture_error = Capture.ERROR, "Kismet exited unexpectedly"
                    st.log("error", st.capture_error)
                else:
                    log.warning("poll failed: %s", exc)
            except Exception:  # never let one bad response kill the poller
                log.exception("unexpected error while polling Kismet")

    def _poll_devices(self, since: int) -> None:
        devices = self.kismet.devices_since(since)
        now = time.monotonic()
        st = self.state
        with st.lock:
            for d in devices:
                old = st.devices.get(d.key)
                if old is None:
                    d.new_until = now + 3
                else:
                    d.new_until = old.new_until
                    if not d.name:
                        d.name = old.name
                    if d.lat == 0 and old.lat:
                        d.lat, d.lon = old.lat, old.lon
                st.devices[d.key] = d
            if devices:
                st.version += 1

    def _poll_sources(self) -> None:
        sources = self.kismet.sources()
        st = self.state
        total = sum(s.packets for s in sources if s.name != "bluetooth")
        now = time.monotonic()
        if self._last_packets:
            t0, p0 = self._last_packets
            if now > t0:
                st.packets_per_sec = max(0.0, (total - p0) / (now - t0))
        self._last_packets = (now, total)
        for s in sources:
            if s.error and s.name not in self._seen_source_errors:
                self._seen_source_errors.add(s.name)
                st.log("error", f"{s.name}: {s.error}")
            elif not s.error and s.name in self._seen_source_errors:
                self._seen_source_errors.discard(s.name)
                st.log("good", f"{s.name}: recovered")
        st.sources = sources
        try:
            st.kismet_rss_kb = int(self.kismet.status().get("kismet.system.memory.rss", 0))
        except KismetError:
            pass

    def _poll_messages(self) -> None:
        msgs = self.kismet.messages_since(self._last_msg_time)
        for m in msgs:
            t = int(m.get("kismet.messagebus.message_time", 0))
            self._last_msg_time = max(self._last_msg_time, t + 1)
            text = m.get("kismet.messagebus.message_string", "")
            flags = int(m.get("kismet.messagebus.message_flags", 0))
            # Kismet flags: 1 debug, 2 info, 4 error, 8 alert, 16 fatal
            if not text or flags & 1:
                continue
            level = "error" if flags & (4 | 16) else "warn" if flags & 8 else "info"
            if level == "info" and not any(k in text.lower() for k in _MSG_INFO_KEEP):
                continue
            self.state.log(level, text)
