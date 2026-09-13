"""Configuration loaded from TOML, with defaults that match scripts/install.sh."""

from __future__ import annotations

import logging
import os
import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path

log = logging.getLogger(__name__)

SEARCH_PATHS = [Path("/etc/wardrive/wardrive.toml")]


@dataclass
class DisplayConfig:
    backend: str = "fbdev"  # "fbdev" (/dev/fb0) or "kmsdrm" (SDL straight to DRM/KMS)
    rotate: int = 180  # 0 or 180; applied only on the device, not in --window mode
    fps: int = 15


@dataclass
class TouchConfig:
    device_name: str = "ADS7846 Touchscreen"
    calibration_file: str = "~/.config/wardrive/touch.json"


@dataclass
class KismetConfig:
    url: str = "http://127.0.0.1:2501"
    auth_file: str = "~/.kismet/kismet_httpd.conf"
    service: str = "wardrive-kismet.service"
    poll_seconds: float = 2.0


@dataclass
class MapConfig:
    # Offline map files built by tools/build_map.py. The first *.map here is used.
    dir: str = "/var/lib/wardrive/maps"
    default_zoom: int = 16


@dataclass
class CaptureConfig:
    # The Pi has no RTC: after a boot away from Wi-Fi the clock is wrong until chrony
    # takes time from the GPS. Capturing before then stamps every record with that
    # wrong time, so START waits for a synced clock unless this is turned off.
    wait_for_clock: bool = True


@dataclass
class GpsConfig:
    host: str = "127.0.0.1"
    port: int = 2947


@dataclass
class WigleConfig:
    enabled: bool = False
    api_name: str = ""
    api_token: str = ""
    donate: bool = False


@dataclass
class HomeConfig:
    enabled: bool = False
    label: str = "Home server"
    url: str = ""
    token: str = ""  # sent as "Authorization: Bearer <token>" when set
    headers: dict = field(default_factory=dict)  # extra headers, e.g. Cloudflare Access service token


@dataclass
class UploadConfig:
    wigle: WigleConfig = field(default_factory=WigleConfig)
    home: HomeConfig = field(default_factory=HomeConfig)


@dataclass
class Config:
    units: str = "imperial"  # or "metric"
    log_dir: str = "/var/lib/wardrive/logs"
    state_dir: str = "~/.local/state/wardrive"
    display: DisplayConfig = field(default_factory=DisplayConfig)
    touch: TouchConfig = field(default_factory=TouchConfig)
    kismet: KismetConfig = field(default_factory=KismetConfig)
    capture: CaptureConfig = field(default_factory=CaptureConfig)
    map: MapConfig = field(default_factory=MapConfig)
    gps: GpsConfig = field(default_factory=GpsConfig)
    upload: UploadConfig = field(default_factory=UploadConfig)

    @property
    def calibration_path(self) -> Path:
        return Path(self.touch.calibration_file).expanduser()

    @property
    def state_path(self) -> Path:
        return Path(self.state_dir).expanduser()


def _apply(obj, data: dict, prefix: str = "") -> None:
    known = {f.name: f for f in fields(obj)}
    for key, value in data.items():
        if key not in known:
            log.warning("unknown config key %s%s", prefix, key)
            continue
        current = getattr(obj, key)
        if is_dataclass(current):
            if isinstance(value, dict):
                _apply(current, value, f"{prefix}{key}.")
            else:
                log.warning("config key %s%s should be a table", prefix, key)
        else:
            setattr(obj, key, type(current)(value))


def load(path: str | os.PathLike | None = None) -> Config:
    cfg = Config()
    candidates = [Path(path)] if path else [Path(p) for p in filter(None, [os.environ.get("WARDRIVE_CONFIG")])] + SEARCH_PATHS
    for candidate in candidates:
        if candidate.is_file():
            with candidate.open("rb") as fh:
                _apply(cfg, tomllib.load(fh))
            log.info("loaded config from %s", candidate)
            break
    if cfg.display.backend not in ("fbdev", "kmsdrm"):
        log.warning("display.backend must be fbdev or kmsdrm, got %s; using fbdev", cfg.display.backend)
        cfg.display.backend = "fbdev"
    if cfg.display.rotate not in (0, 180):
        log.warning("display.rotate must be 0 or 180, got %s; using 180", cfg.display.rotate)
        cfg.display.rotate = 180
    return cfg
