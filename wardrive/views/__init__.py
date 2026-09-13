"""Screens shown in the content area (or fullscreen) of the app."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pygame

from .. import theme
from ..touch import TouchEvent
from ..widgets import Button

if TYPE_CHECKING:
    from ..app import App

# Frame layout on the 480x320 logical canvas.
STATUS = pygame.Rect(0, 0, 480, 26)
CONTENT = pygame.Rect(0, 26, 368, 268)
NAV = pygame.Rect(372, 30, 104, 260)
FOOTER = pygame.Rect(0, 294, 480, 26)
FULL = pygame.Rect(0, 0, 480, 320)


class View:
    name = ""
    fullscreen = False  # hides status bar, nav column and footer

    def __init__(self, app: "App"):
        self.app = app
        self.buttons: list[Button] = []

    @property
    def state(self):
        return self.app.state

    def on_show(self) -> None:
        pass

    def handle(self, ev: TouchEvent) -> bool:
        return any(b.handle(ev) for b in self.buttons)

    def update(self, now: float) -> None:
        for b in self.buttons:
            b.update(now)

    def draw(self, surf: pygame.Surface, now: float) -> None:
        for b in self.buttons:
            b.draw(surf, now)


class Modal:
    """Centered panel over the current view; a tap anywhere closes it."""

    rect = pygame.Rect(30, 30, 420, 260)
    title = ""

    def __init__(self, app: "App"):
        self.app = app

    def handle(self, ev: TouchEvent) -> bool:
        if ev.kind == "up":
            self.app.close_modal()
        return True

    def update(self, now: float) -> None:
        pass

    def lines(self) -> list[tuple[str, str, tuple]]:
        """(label, value, value_color) rows."""
        return []

    def draw(self, surf: pygame.Surface, now: float) -> None:
        shade = pygame.Surface(surf.get_size(), pygame.SRCALPHA)
        shade.fill((0, 0, 0, 170))
        surf.blit(shade, (0, 0))
        pygame.draw.rect(surf, theme.PANEL, self.rect, border_radius=10)
        pygame.draw.rect(surf, theme.BORDER, self.rect, 1, border_radius=10)
        x, y = self.rect.x + 16, self.rect.y + 12
        theme.blit_text(surf, theme.fit(self.title, 18, self.rect.w - 32, bold=True), (x, y), 18, bold=True)
        y += 32
        lines = self.lines()
        # Fit every row above the "tap to close" hint (8 rows at 22 px; tighter beyond that).
        step = min(22, (self.rect.bottom - 24 - y) // max(1, len(lines)))
        for label, value, color in lines:
            theme.blit_text(surf, label, (x, y), 13, theme.DIM)
            v = theme.fit(value, 14, self.rect.w - 130)
            theme.blit_text(surf, v, (x + 96, y), 14, color)
            y += step
        theme.blit_text(surf, "tap to close", (self.rect.centerx, self.rect.bottom - 8), 11, theme.DIM, anchor="midbottom")
