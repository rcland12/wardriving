"""Pi health: temperature, power, disk, network, clock source."""

from __future__ import annotations

import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

from .state import State


def _run(*cmd: str) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=3).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def cpu_temp() -> float:
    try:
        return int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000
    except (OSError, ValueError):
        return 0.0


def throttled() -> int:
    out = _run("vcgencmd", "get_throttled")  # "throttled=0x50005"
    try:
        return int(out.split("=", 1)[1], 16)
    except (IndexError, ValueError):
        return 0


def primary_ip() -> str:
    """Source address used for the default route (no packets are sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("1.1.1.1", 53))
            return s.getsockname()[0]
    except OSError:
        return ""


def online(timeout: float = 1.5) -> bool:
    try:
        socket.create_connection(("1.1.1.1", 443), timeout=timeout).close()
        return True
    except OSError:
        return False


SYNCED_CLOCK_SOURCES = ("GPS", "NTP")


def clock_source() -> str:
    """"GPS" or "NTP" when chrony has set the clock, "unsynced" if not, "" if unknown."""
    # chronyc -c tracking: refid,refname,stratum,...,leap status (last field)
    fields = _run("chronyc", "-c", "tracking").split(",")
    if len(fields) < 2:
        return ""
    if fields[-1].strip() == "Not synchronised":
        return "unsynced"
    return "GPS" if fields[1] == "GPS" else "NTP"


class SysInfoPoller(threading.Thread):
    def __init__(self, state: State, log_dir: str, interval: float = 5.0):
        super().__init__(name="sysinfo", daemon=True)
        self.state, self.log_dir, self.interval = state, log_dir, interval

    def run(self) -> None:
        tick = 0
        while True:
            s = self.state.sys
            s.cpu_temp = cpu_temp()
            flags = throttled()
            s.undervolt_now = bool(flags & 0x1)
            s.throttled_now = bool(flags & 0x4)
            s.undervolt_since_boot = bool(flags & 0x10000)
            try:
                s.disk_free_bytes = shutil.disk_usage(self.log_dir).free
            except OSError:
                s.disk_free_bytes = 0
            s.clock_source = clock_source()
            if tick % 3 == 0:  # every ~15 s
                s.ip = primary_ip()
                s.online = online() if s.ip else False
            tick += 1
            time.sleep(self.interval)
