"""Small formatting helpers shared by the views."""

from __future__ import annotations

from datetime import datetime


def duration(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def distance(meters: float, units: str) -> str:
    if units == "metric":
        return f"{meters / 1000:.2f} km"
    return f"{meters / 1609.344:.2f} mi"


def speed(mps: float, units: str) -> str:
    if units == "metric":
        return f"{mps * 3.6:.0f} km/h"
    return f"{mps * 2.23694:.0f} mph"


def altitude(m: float, units: str) -> str:
    return f"{m:.0f} m" if units == "metric" else f"{m * 3.28084:.0f} ft"


def cardinal(deg: float) -> str:
    names = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return names[int((deg % 360) / 45 + 0.5) % 8]


def count(n: int) -> str:
    return f"{n:,}"


def local_time(iso_utc: str) -> str:
    """gpsd ISO8601 UTC timestamp -> local HH:MM:SS."""
    try:
        return datetime.fromisoformat(iso_utc.replace("Z", "+00:00")).astimezone().strftime("%H:%M:%S")
    except ValueError:
        return "--:--:--"
