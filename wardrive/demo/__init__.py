"""Demo mode: fake sessions, simulated GPS and simulated capture for trying the UI anywhere.

Everything lives under one directory (config demo.dir, /var/lib/wardrive/demo on the Pi):

    enabled        marker: the UI starts in demo mode while this exists
    logs/          generated sessions (.kismet + .wiglecsv), and any captured in demo mode
    state/         upload records and session caches for demo data

Real capture logs, upload records and settings are never read or written in demo mode, and
turning it off deletes this one directory. Switch with MENU -> DEMO, or on the Pi:

    python3 -m wardrive.demo on|off|status|regenerate
"""

from __future__ import annotations

import json
import logging
import math
import random
import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from ..config import Config

log = logging.getLogger(__name__)

STALE_CLOCK_S = 27_961  # how far behind the Pi's clock was on a real no-RTC boot


@dataclass
class Drive:
    days_ago: int
    hour: float  # local time the drive starts
    minutes: int
    away: bool = False  # a trip to another city instead of around home
    stale_clock: bool = False  # booted away from Wi-Fi: the clock is wrong for the first minutes


PLAN = [
    Drive(19, 18.5, 22),
    Drive(16, 12.2, 9),
    Drive(13, 17.8, 47),
    Drive(11, 19.1, 14, stale_clock=True),
    Drive(8, 10.4, 63, away=True),
    Drive(6, 20.0, 31),
    Drive(3, 16.6, 18),
    Drive(1, 18.9, 40),
]
ATLANTA = (33.7490, -84.3880)
SAVANNAH = (32.0809, -81.0912)


def root(cfg: Config) -> Path:
    return Path(cfg.demo.dir).expanduser()


def is_on(cfg: Config) -> bool:
    return (root(cfg) / "enabled").is_file()


def use_demo_paths(cfg: Config) -> None:
    """Point the app's logs and state at the demo directory."""
    cfg.log_dir = str(root(cfg) / "logs")
    cfg.state_dir = str(root(cfg) / "state")


def away_point(lat: float, lon: float) -> tuple[float, float]:
    near_atlanta = math.dist((lat, lon), ATLANTA) < 0.5
    return SAVANNAH if near_atlanta else ATLANTA


def generate(cfg: Config, progress: Callable[[int, int, str], None] | None = None, seed: int = 1,
             plan: list[Drive] | None = None, now: float | None = None) -> list[Path]:
    """Simulate the drives in `plan` and write them to <demo dir>/logs."""
    from ..mapdata import find_map
    from .files import SessionRecorder
    from .world import MapIndex, Scanner, World, make_driver

    plan = PLAN if plan is None else plan
    now = time.time() if now is None else now
    logs = root(cfg) / "logs"
    mapfile = find_map(cfg.map.dir)
    index = MapIndex(mapfile) if mapfile else None
    world = World(index, seed)
    home = (cfg.demo.lat, cfg.demo.lon)
    written: list[Path] = []
    try:
        for i, drive in enumerate(plan):
            rng = random.Random(seed * 1000 + i)
            day = datetime.fromtimestamp(now - drive.days_ago * 86400).replace(hour=0, minute=0, second=0, microsecond=0)
            start = day.timestamp() + drive.hour * 3600 + rng.uniform(0, 600)
            lat, lon = away_point(*home) if drive.away else home
            recorder = SessionRecorder(start, seed=rng.randrange(2**31),
                                       clock_error=STALE_CLOCK_S if drive.stale_clock else 0.0,
                                       clock_fixed_after=rng.uniform(120, 240) if drive.stale_clock else 0.0)
            if progress:
                progress(i + 1, len(plan), recorder.name)
            simulate(recorder, make_driver(index, lat, lon, rng), Scanner(world, rng), start, drive.minutes * 60)
            written += recorder.write(logs, modified=recorder.ended + 30)
    finally:
        if mapfile:
            mapfile.close()
    return written


def simulate(recorder, driver, scanner, start: float, seconds: float, step: float = 1.0) -> float:
    """Drive for `seconds`, recording everything heard. Returns metres driven."""
    driven = 0.0
    alt = 95.0
    recorder.message(start, "Data source 'alfa' launched successfully")
    recorder.message(start, "Data source 'bluetooth' launched successfully")
    t = start
    while t < start + seconds:
        driven += driver.step(step)
        t += step
        alt += (scanner.rng.random() - 0.5) * 0.4
        lat, lon = driver.lat, driver.lon
        recorder.position(t, lat, lon, alt, driver.speed, driver.heading)
        for device, rssi in scanner.scan(t, lat, lon, driver.speed):
            recorder.observe(t, device, rssi, lat, lon, alt, driver.speed, driver.heading)
    return driven


def turn_on(cfg: Config, progress: Callable[[int, int, str], None] | None = None, seed: int = 1) -> Path:
    """Create the demo directory, generate sessions unless some exist, and set the marker."""
    base = root(cfg)
    (base / "logs").mkdir(parents=True, exist_ok=True)
    (base / "state").mkdir(parents=True, exist_ok=True)
    if not any((base / "logs").glob("*.kismet")):
        generate(cfg, progress, seed=seed)
    (base / "enabled").write_text(json.dumps({
        "enabled_at": datetime.now().isoformat(timespec="seconds"), "home": [cfg.demo.lat, cfg.demo.lon],
    }) + "\n")
    return base


def turn_off(cfg: Config) -> bool:
    """Delete the demo directory. Returns False if there was nothing to delete."""
    base = root(cfg)
    if not base.exists():
        return False
    # Refuse anything that doesn't look like a demo directory, in case demo.dir is misconfigured.
    if "demo" not in base.name or not ((base / "enabled").exists() or (base / "logs").exists()):
        raise RuntimeError(f"{base} doesn't look like a demo directory; not deleting it")
    shutil.rmtree(base)
    return True


def regenerate(cfg: Config, progress: Callable[[int, int, str], None] | None = None, seed: int | None = None) -> Path:
    base = root(cfg)
    for sub in ("logs", "state"):
        shutil.rmtree(base / sub, ignore_errors=True)
    return turn_on(cfg, progress, seed=seed if seed is not None else random.randrange(1, 10_000))


def status(cfg: Config) -> list[str]:
    base = root(cfg)
    logs = sorted((base / "logs").glob("*.kismet")) if base.exists() else []
    size = sum(p.stat().st_size for p in (base / "logs").iterdir()) if logs else 0
    return [
        f"demo mode: {'ON' if is_on(cfg) else 'off'}",
        f"directory: {base}{'' if base.exists() else ' (not created)'}",
        f"sessions:  {len(logs)} ({size / 1e6:.1f} MB)",
        f"start:     {cfg.demo.lat:.4f}, {cfg.demo.lon:.4f} (demo.lat / demo.lon in wardrive.toml)",
    ]
