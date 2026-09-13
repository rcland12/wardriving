"""MENU: uploads, touch calibration, power, and system info."""

from __future__ import annotations

import time

import pygame

from .. import __version__, fmt, theme
from ..state import Capture
from ..widgets import Button
from . import CONTENT, View

HOLD = 2.0


def _uptime() -> float:
    try:
        with open("/proc/uptime") as fh:
            return float(fh.read().split()[0])
    except (OSError, ValueError):
        return 0.0


class MenuView(View):
    name = "menu"

    def __init__(self, app):
        super().__init__(app)
        x0, y0, w, h, gap = CONTENT.x + 4, CONTENT.y + 4, 178, 54, 4
        backend = app.backend
        self.buttons = [
            Button((x0, y0, w, h), "UPLOAD", lambda: app.show("upload"), sublabel="WiGLE / home server"),
            Button((x0 + w + gap, y0, w, h), "CALIBRATE", lambda: app.show("calibrate"), sublabel="touch screen"),
            Button(
                (x0, y0 + h + gap, w, h), "REBOOT", backend.reboot, hold=HOLD,
                sublabel="hold 2 s", color=theme.PANEL,
            ),
            Button(
                (x0 + w + gap, y0 + h + gap, w, h), "SHUTDOWN", self._shutdown, hold=HOLD,
                sublabel=lambda: "hold 2 s · stops capture" if app.state.capture == Capture.RUNNING else "hold 2 s",
                color=theme.DARK_RED,
            ),
        ]

    def _shutdown(self):
        self.app.shutting_down = time.monotonic()
        self.app.backend.poweroff()

    def draw(self, surf: pygame.Surface, now: float) -> None:
        super().draw(surf, now)
        s = self.state.sys
        rows = [
            ("IP address", s.ip or "offline"),
            ("Internet", "yes" if s.online else "no"),
            ("Clock", s.clock_source or "--"),
            ("CPU temp", f"{s.cpu_temp:.0f} °C" if s.cpu_temp else "--"),
            ("Power", "UNDER-VOLTAGE" if s.undervolt_now else ("dipped since boot" if s.undervolt_since_boot else "ok")),
            ("Disk free", fmt.size(s.disk_free_bytes) if s.disk_free_bytes else "--"),
            ("Uptime", fmt.duration(_uptime())),
            ("Version", f"wardrive {__version__}" + (" (mock)" if self.app.backend.mock else "")),
        ]
        y = CONTENT.y + 126
        pygame.draw.line(surf, theme.BORDER, (6, y - 6), (CONTENT.right - 6, y - 6))
        for i, (label, value) in enumerate(rows):
            col_x = CONTENT.x + 8 + (i % 2) * 180
            row_y = y + (i // 2) * 34
            theme.blit_text(surf, label, (col_x, row_y), 11, theme.DIM)
            color = theme.RED if value == "UNDER-VOLTAGE" else theme.TEXT
            theme.blit_text(surf, theme.fit(value, 14, 172), (col_x, row_y + 14), 14, color)
