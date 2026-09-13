"""Touch input: raw evdev reader for the resistive panel, plus calibration.

The reader thread only produces raw controller coordinates. The UI thread maps
them through a Calibration (an affine fit), which absorbs axis swap, axis
inversion and the 180° display rotation in one step.
"""

from __future__ import annotations

import json
import logging
import queue
import statistics
import threading
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class TouchEvent:
    kind: str  # "down" | "move" | "up"
    pos: tuple[int, int]  # logical screen coordinates
    raw: tuple[int, int]  # controller coordinates (== pos for mouse input)


# --- calibration -----------------------------------------------------------------


def _solve3(m: list[list[float]], v: list[float]) -> list[float]:
    """Solve a 3x3 linear system by Gaussian elimination with partial pivoting."""
    a = [row[:] + [v[i]] for i, row in enumerate(m)]
    for col in range(3):
        pivot = max(range(col, 3), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-12:
            raise ValueError("degenerate calibration points")
        a[col], a[pivot] = a[pivot], a[col]
        for r in range(3):
            if r != col:
                f = a[r][col] / a[col][col]
                for c in range(col, 4):
                    a[r][c] -= f * a[col][c]
    return [a[i][3] / a[i][i] for i in range(3)]


class Calibration:
    """screen_x = a*rx + b*ry + c ; screen_y = d*rx + e*ry + f"""

    def __init__(self, coeffs: tuple[float, ...], size: tuple[int, int] = (480, 320)):
        self.coeffs = tuple(coeffs)
        self.size = size

    @classmethod
    def fit(cls, raw: list[tuple[float, float]], screen: list[tuple[float, float]], size=(480, 320)) -> "Calibration":
        if len(raw) < 3:
            raise ValueError("need at least 3 points")
        # Least squares via normal equations: (MᵀM) p = Mᵀ t
        mtm = [[0.0] * 3 for _ in range(3)]
        mtx, mty = [0.0] * 3, [0.0] * 3
        for (rx, ry), (sx, sy) in zip(raw, screen):
            row = (rx, ry, 1.0)
            for i in range(3):
                for j in range(3):
                    mtm[i][j] += row[i] * row[j]
                mtx[i] += row[i] * sx
                mty[i] += row[i] * sy
        return cls((*_solve3(mtm, mtx), *_solve3(mtm, mty)), size)

    @classmethod
    def identity(cls, size=(480, 320)) -> "Calibration":
        return cls((1, 0, 0, 0, 1, 0), size)

    def map(self, rx: float, ry: float) -> tuple[int, int]:
        a, b, c, d, e, f = self.coeffs
        x = min(max(round(a * rx + b * ry + c), 0), self.size[0] - 1)
        y = min(max(round(d * rx + e * ry + f), 0), self.size[1] - 1)
        return x, y

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"coeffs": self.coeffs, "size": self.size}, indent=2))

    @classmethod
    def load(cls, path: Path) -> "Calibration | None":
        try:
            data = json.loads(path.read_text())
            return cls(tuple(data["coeffs"]), tuple(data.get("size", (480, 320))))
        except (OSError, ValueError, KeyError, TypeError):
            return None


# --- evdev reader ----------------------------------------------------------------


class EvdevTouch(threading.Thread):
    """Reads a single-touch absolute device and queues ("down"/"move"/"up", rx, ry)."""

    SETTLE_SAMPLES = 2  # resistive panels are noisy for the first reports of a press

    def __init__(self, device_name: str):
        super().__init__(name="touch", daemon=True)
        self.device_name = device_name
        self.events: queue.Queue[tuple[str, int, int]] = queue.Queue()
        self.connected = False

    def _find(self):
        import evdev

        for path in evdev.list_devices():
            dev = evdev.InputDevice(path)
            if dev.name == self.device_name:
                return dev
            dev.close()
        return None

    def run(self) -> None:
        from evdev import ecodes

        while True:
            dev = None
            try:
                dev = self._find()
            except OSError as exc:
                log.warning("touch device scan failed: %s", exc)
            if dev is None:
                self.connected = False
                time.sleep(3)
                continue
            log.info("touch device: %s (%s)", dev.name, dev.path)
            self.connected = True
            x = y = 0
            touching = False
            samples: list[tuple[int, int]] = []
            down_sent = False
            try:
                for ev in dev.read_loop():
                    if ev.type == ecodes.EV_ABS:
                        if ev.code == ecodes.ABS_X:
                            x = ev.value
                        elif ev.code == ecodes.ABS_Y:
                            y = ev.value
                    elif ev.type == ecodes.EV_KEY and ev.code == ecodes.BTN_TOUCH:
                        if ev.value:
                            touching, samples, down_sent = True, [], False
                        else:
                            if samples:
                                tail = samples[-5:]
                                rx = int(statistics.median(p[0] for p in tail))
                                ry = int(statistics.median(p[1] for p in tail))
                                if not down_sent:
                                    self.events.put(("down", rx, ry))
                                self.events.put(("up", rx, ry))
                            touching = False
                    elif ev.type == ecodes.EV_SYN and touching:
                        samples.append((x, y))
                        if not down_sent and len(samples) >= self.SETTLE_SAMPLES:
                            rx = sum(p[0] for p in samples) // len(samples)
                            ry = sum(p[1] for p in samples) // len(samples)
                            self.events.put(("down", rx, ry))
                            down_sent = True
                        elif down_sent:
                            self.events.put(("move", x, y))
            except OSError as exc:
                log.warning("touch device lost: %s", exc)
                self.connected = False
                time.sleep(1)
