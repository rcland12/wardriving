"""Fullscreen 4-point touch calibration with a verification tap and a test pad.

Calibrating in the final physical orientation means the fit absorbs the 180°
rotation, any axis swap/inversion, and the panel's real raw min/max.
"""

from __future__ import annotations

import math
import time

import pygame

from .. import theme
from ..touch import Calibration, TouchEvent
from . import FULL, View

INSET = 30
TARGETS = [(INSET, INSET), (480 - INSET, INSET), (480 - INSET, 320 - INSET), (INSET, 320 - INSET)]
VERIFY = (240, 160)
MAX_ERROR = 30  # px allowed on the verification tap
MIN_RAW_SPREAD = 150  # raw units; closer consecutive taps are treated as a double tap
IDLE_TIMEOUT = 30  # s; only when a previous calibration exists to fall back to
DONE = pygame.Rect(170, 250, 140, 56)


class CalibrateView(View):
    name = "calibrate"
    fullscreen = True

    def __init__(self, app):
        super().__init__(app)
        self.reset()

    def reset(self) -> None:
        self.phase = "collect"  # collect -> verify -> test
        self.raws: list[tuple[int, int]] = []
        self.candidate: Calibration | None = None
        self.message = ""
        self.test_points: list[tuple[int, int]] = []
        self.last_touch = time.monotonic()

    def on_show(self) -> None:
        self.previous = self.app.calibration
        self.reset()

    def handle(self, ev: TouchEvent) -> bool:
        self.last_touch = time.monotonic()
        if ev.kind != "up":
            return True
        if self.phase == "collect":
            if self.raws and math.dist(self.raws[-1], ev.raw) < MIN_RAW_SPREAD:
                return True
            self.raws.append(ev.raw)
            self.message = ""
            if len(self.raws) == len(TARGETS):
                try:
                    self.candidate = Calibration.fit(self.raws, TARGETS)
                    self.phase = "verify"
                except ValueError:
                    self.reset()
                    self.message = "Taps too close together. Try again."
        elif self.phase == "verify":
            err = math.dist(self.candidate.map(*ev.raw), VERIFY)
            if err <= MAX_ERROR:
                self.candidate.save(self.app.cfg.calibration_path)
                self.app.calibration = self.candidate
                self.phase = "test"
            else:
                self.reset()
                self.message = f"Off by {err:.0f} px. Let's redo it."
        else:  # test: events are now mapped with the new calibration
            if DONE.inflate(20, 20).collidepoint(ev.pos):
                self.app.show("nets")
            else:
                self.test_points = (self.test_points + [ev.pos])[-40:]
        return True

    def update(self, now: float) -> None:
        idle = time.monotonic() - self.last_touch > IDLE_TIMEOUT
        if idle and self.previous is not None and self.phase != "test":
            self.app.calibration = self.previous
            self.app.show("menu")

    def draw(self, surf: pygame.Surface, now: float) -> None:
        surf.fill(theme.BG)
        if not self.app.touch_connected():
            theme.blit_text(surf, "Waiting for touch device…", FULL.center, 18, theme.AMBER, anchor="center")
            return
        if self.phase == "test":
            theme.blit_text(surf, "Calibration saved", (240, 40), 22, theme.GREEN, bold=True, anchor="center")
            theme.blit_text(surf, "Tap around to check accuracy", (240, 70), 14, theme.DIM, anchor="center")
            for p in self.test_points:
                pygame.draw.circle(surf, theme.BLUE, p, 4)
            pygame.draw.rect(surf, theme.DARK_GREEN, DONE, border_radius=8)
            pygame.draw.rect(surf, theme.GREEN, DONE, 1, border_radius=8)
            theme.blit_text(surf, "DONE", DONE.center, 20, theme.TEXT, bold=True, anchor="center")
            return

        if self.phase == "collect":
            target = TARGETS[len(self.raws)]
            step = f"Tap the crosshair ({len(self.raws) + 1}/{len(TARGETS)})"
        else:
            target = VERIFY
            step = "Now tap the center crosshair to verify"
        _crosshair(surf, target, now)
        theme.blit_text(surf, "TOUCH CALIBRATION", (240, 110), 20, theme.TEXT, bold=True, anchor="center")
        theme.blit_text(surf, step, (240, 138) if self.phase == "collect" else (240, 210), 15, theme.TEXT, anchor="center")
        hint = "Use the stylus and tap precisely"
        if self.message:
            theme.blit_text(surf, self.message, (240, 234), 14, theme.AMBER, anchor="center")
        else:
            theme.blit_text(surf, hint, (240, 234), 12, theme.DIM, anchor="center")


def _crosshair(surf, pos, now):
    x, y = pos
    pulse = 12 + 3 * math.sin(now * 6)
    pygame.draw.circle(surf, theme.RED, pos, int(pulse), 2)
    pygame.draw.line(surf, theme.TEXT, (x - 20, y), (x + 20, y), 1)
    pygame.draw.line(surf, theme.TEXT, (x, y - 20), (x, y + 20), 1)
    pygame.draw.circle(surf, theme.TEXT, pos, 2)
