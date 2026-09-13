"""UPLOAD: push finished sessions to WiGLE or the home server."""

from __future__ import annotations

import time

import pygame

from .. import fmt, theme
from ..touch import TouchEvent
from ..widgets import Button, DragScroll
from . import CONTENT, View

ROW_H = 24
LIST = pygame.Rect(CONTENT.x, CONTENT.y + 96, CONTENT.w, CONTENT.h - 96)
TARGETS = ("wigle", "home")


class UploadView(View):
    name = "upload"

    def __init__(self, app):
        super().__init__(app)
        self.scroll = DragScroll(LIST, ROW_H)
        self._sessions = []
        self._pending: dict[str, int] = {}
        self._done: set[tuple[str, str]] = set()
        self._was_busy = False
        self._refreshed = 0.0
        up = app.backend.uploads
        x0, y0 = CONTENT.x + 4, CONTENT.y + 4
        self.buttons = [Button((x0, y0, 58, 54), "BACK", lambda: app.show("menu"), size=12)]
        for i, target in enumerate(TARGETS):
            self.buttons.append(
                Button(
                    (x0 + 62 + i * 150, y0, 146, 54),
                    lambda t=target: up.label(t).upper()[:12],
                    lambda t=target: self._upload(t),
                    sublabel=lambda t=target: self._sub(t),
                    enabled=lambda t=target: up.enabled(t) and not self.state.upload_busy,
                    size=14,
                )
            )

    def on_show(self) -> None:
        self._refresh(force=True)

    def _refresh(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._refreshed < 5:
            return
        up = self.app.backend.uploads
        self._sessions = up.sessions()
        self._pending = {t: len(up.pending(t)) for t in TARGETS}
        self._done = {(s.name, t) for s in self._sessions for t in TARGETS if up.uploaded(s.name, t)}
        self._refreshed = now

    def _sub(self, target: str) -> str:
        if not self.app.backend.uploads.enabled(target):
            return "not configured"
        n = self._pending.get(target, 0)
        return f"{n} pending" if n else "up to date"

    def _upload(self, target: str) -> None:
        if not self.state.sys.online and not self.app.backend.mock:
            self.state.upload_status = "No internet. Connect to home Wi-Fi first."
            return
        self.app.backend.uploads.upload(target)

    def handle(self, ev: TouchEvent) -> bool:
        if super().handle(ev):
            return True
        consumed, _ = self.scroll.handle(ev)
        return consumed

    def draw(self, surf: pygame.Surface, now: float) -> None:
        self._refresh(force=self._was_busy and not self.state.upload_busy)
        self._was_busy = self.state.upload_busy
        super().draw(surf, now)

        status = self.state.upload_status or ("Online" if self.state.sys.online else "Offline: uploads need home Wi-Fi")
        color = theme.AMBER if self.state.upload_busy else theme.DIM
        theme.blit_text(surf, theme.fit(status, 12, CONTENT.w - 12), (CONTENT.x + 6, CONTENT.y + 64), 12, color)

        hy = LIST.y - 16
        theme.blit_text(surf, "SESSION", (6, hy), 11, theme.DIM, bold=True)
        theme.blit_text(surf, "ROWS", (206, hy), 11, theme.DIM, bold=True, anchor="topright")
        theme.blit_text(surf, "SIZE", (276, hy), 11, theme.DIM, bold=True, anchor="topright")
        theme.blit_text(surf, "W  H", (296, hy), 11, theme.DIM, bold=True)
        pygame.draw.line(surf, theme.BORDER, (0, LIST.y - 2), (CONTENT.right, LIST.y - 2))

        if not self._sessions:
            theme.blit_text(surf, "No finished sessions yet", LIST.center, 14, theme.DIM, anchor="center")
            return
        self.scroll.clamp(len(self._sessions), LIST.h)
        clip = surf.get_clip()
        surf.set_clip(LIST)
        for i, s in enumerate(self._sessions):
            y = LIST.y + i * ROW_H - self.scroll.offset
            if y + ROW_H < LIST.y or y > LIST.bottom:
                continue
            if i % 2:
                pygame.draw.rect(surf, theme.PANEL, (0, y, LIST.w, ROW_H))
            cy = y + ROW_H // 2
            theme.blit_text(surf, s.started, (6, cy), 13, theme.TEXT, mono=True, anchor="midleft")
            theme.blit_text(surf, fmt.count(s.wigle_rows), (206, cy), 13, theme.TEXT, mono=True, anchor="midright")
            theme.blit_text(surf, fmt.size(s.size), (276, cy), 12, theme.DIM, anchor="midright")
            for j, t in enumerate(TARGETS):
                done = (s.name, t) in self._done
                pygame.draw.circle(surf, theme.GREEN if done else theme.BORDER, (300 + j * 20, cy), 5, 0 if done else 1)
        surf.set_clip(clip)
