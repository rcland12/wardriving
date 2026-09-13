"""Touch widgets sized for a resistive 3.5" panel."""

from __future__ import annotations

import time
from typing import Callable

import pygame

from . import theme
from .touch import TouchEvent

SLOP = 12  # px a finger may drift off a button and still count as a tap

Label = str | Callable[[], str]


def _resolve(v):
    return v() if callable(v) else v


class Button:
    def __init__(
        self,
        rect: tuple[int, int, int, int] | pygame.Rect,
        label: Label,
        on_tap: Callable[[], None] | None = None,
        *,
        hold: float = 0.0,  # seconds the button must be held to fire (0 = tap)
        size: int = 15,
        color=theme.PANEL,
        fg=theme.TEXT,
        active: Callable[[], bool] | None = None,  # highlighted, e.g. current view
        enabled: Callable[[], bool] | None = None,
        sublabel: Label | None = None,
    ):
        self.rect = pygame.Rect(rect)
        self.label, self.on_tap, self.hold = label, on_tap, hold
        self.size, self.color, self.fg = size, color, fg
        self.active, self.enabled, self.sublabel = active, enabled, sublabel
        self.pressed = False
        self._t0 = 0.0
        self._fired = False

    def is_enabled(self) -> bool:
        return self.enabled() if self.enabled else True

    def handle(self, ev: TouchEvent) -> bool:
        if not self.is_enabled():
            return self.rect.collidepoint(ev.pos) and ev.kind == "down"
        if ev.kind == "down":
            if self.rect.collidepoint(ev.pos):
                self.pressed, self._t0, self._fired = True, time.monotonic(), False
                return True
            return False
        if not self.pressed:
            return False
        inside = self.rect.inflate(SLOP * 2, SLOP * 2).collidepoint(ev.pos)
        if ev.kind == "move":
            if not inside:
                self.pressed = False
            return True
        # up
        self.pressed = False
        if inside and not self.hold and self.on_tap:
            self.on_tap()
        return True

    def update(self, now: float) -> None:
        if self.hold and self.pressed and not self._fired and now - self._t0 >= self.hold:
            self._fired = True
            self.pressed = False
            if self.on_tap:
                self.on_tap()

    def draw(self, surf: pygame.Surface, now: float) -> None:
        color = _resolve(self.color)
        if self.active and self.active():
            color = theme.PANEL_HI
        if self.pressed:
            color = tuple(min(255, c + 40) for c in color)
        enabled = self.is_enabled()
        pygame.draw.rect(surf, color, self.rect, border_radius=6)
        pygame.draw.rect(surf, theme.BORDER if not (self.active and self.active()) else theme.BLUE, self.rect, 1, border_radius=6)
        if self.hold and self.pressed:
            frac = min(1.0, (now - self._t0) / self.hold)
            bar = pygame.Rect(self.rect.x + 3, self.rect.bottom - 7, int((self.rect.w - 6) * frac), 4)
            pygame.draw.rect(surf, theme.AMBER, bar, border_radius=2)
        fg = _resolve(self.fg) if enabled else theme.DIM
        label = _resolve(self.label)
        sub = _resolve(self.sublabel) if self.sublabel else ""
        cx, cy = self.rect.center
        if sub:
            theme.blit_text(surf, label, (cx, cy - 2), self.size, fg, bold=True, anchor="midbottom")
            theme.blit_text(surf, sub, (cx, cy + 2), 11, theme.DIM if enabled else theme.BORDER, anchor="midtop")
        else:
            theme.blit_text(surf, label, (cx, cy), self.size, fg, bold=True, anchor="center")


class DragScroll:
    """Vertical drag-to-scroll with tap detection for list-like areas."""

    TAP_SLOP = 10

    def __init__(self, rect: pygame.Rect, row_h: int):
        self.rect, self.row_h = rect, row_h
        self.offset = 0  # px
        self._start: tuple[int, int] | None = None
        self._start_offset = 0
        self._dragging = False

    def clamp(self, n_rows: int, visible_h: int) -> None:
        max_off = max(0, n_rows * self.row_h - visible_h)
        self.offset = min(max(self.offset, 0), max_off)

    def handle(self, ev: TouchEvent) -> tuple[bool, int | None]:
        """Returns (consumed, tapped_row_index)."""
        if ev.kind == "down":
            if not self.rect.collidepoint(ev.pos):
                return False, None
            self._start, self._start_offset, self._dragging = ev.pos, self.offset, False
            return True, None
        if self._start is None:
            return False, None
        dy = ev.pos[1] - self._start[1]
        if ev.kind == "move":
            if abs(dy) > self.TAP_SLOP:
                self._dragging = True
            if self._dragging:
                self.offset = self._start_offset - dy
            return True, None
        start, dragging = self._start, self._dragging
        self._start = None
        if dragging:
            return True, None
        row = (start[1] - self.rect.y + self.offset) // self.row_h
        return True, row
