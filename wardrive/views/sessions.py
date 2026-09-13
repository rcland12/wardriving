"""SESSIONS: browse finished capture sessions and every network they recorded."""

from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Callable

import pygame

from .. import fmt, theme
from ..sessions import SessionInfo, sort_devices
from ..touch import TouchEvent
from ..widgets import Button, DragScroll
from . import CONTENT, View
from .devicelist import FILTERS, DeviceList, DeviceModal

TOOLBAR_Y = CONTENT.y + 3
SESSION_ROW_H = 44
SESSION_LIST = pygame.Rect(CONTENT.x, CONTENT.y + 32, CONTENT.w, CONTENT.h - 32)
DEVICE_LIST = pygame.Rect(CONTENT.x, CONTENT.y + 46, CONTENT.w, CONTENT.h - 46)
SORTS = [("signal", "SIGNAL"), ("name", "NAME"), ("new", "NEWEST")]


class Loader:
    """Run a slow load off the UI thread; views poll `busy`/`result`."""

    def __init__(self):
        self.result = None
        self.error = ""
        self.busy = False
        self._generation = 0

    def start(self, fn: Callable[[], object]) -> None:
        self._generation += 1
        generation, self.busy, self.error = self._generation, True, ""

        def run():
            try:
                result, error = fn(), ""
            except Exception as exc:  # shown on screen rather than crashing the UI
                result, error = None, str(exc)
            if generation == self._generation:
                self.result, self.error, self.busy = result, error, False

        threading.Thread(target=run, name="loader", daemon=True).start()

    def wait(self, timeout: float = 10.0) -> None:
        end = time.monotonic() + timeout
        while self.busy and time.monotonic() < end:
            time.sleep(0.01)


def session_title(info: SessionInfo) -> str:
    return datetime.fromtimestamp(info.start).strftime("%a %m/%d %H:%M") if info.start else info.name


class SessionsView(View):
    name = "sessions"

    def __init__(self, app):
        super().__init__(app)
        self.loader = Loader()
        self.scroll = DragScroll(SESSION_LIST, SESSION_ROW_H)
        self.buttons = [Button((4, TOOLBAR_Y, 64, 24), "BACK", lambda: app.show("menu"), size=12)]

    def on_show(self) -> None:
        self.loader.start(self.app.backend.sessions.sessions)

    def entries(self) -> list[SessionInfo | None]:
        """None stands for the combined "All sessions" row."""
        sessions = self.loader.result or []
        return ([None] if len(sessions) > 1 else []) + list(sessions)

    def handle(self, ev: TouchEvent) -> bool:
        if super().handle(ev):
            return True
        consumed, row = self.scroll.handle(ev)
        entries = self.entries()
        if row is not None and 0 <= row < len(entries):
            info = entries[row]
            self.app.views["session_nets"].open(
                None if info is None else info.name,
                "All sessions" if info is None else session_title(info),
            )
            self.app.show("session_nets")
        return consumed

    def draw(self, surf: pygame.Surface, now: float) -> None:
        super().draw(surf, now)
        theme.blit_text(surf, "SESSIONS", (78, TOOLBAR_Y + 12), 14, theme.TEXT, bold=True, anchor="midleft")
        sessions = self.loader.result or []
        status = "loading…" if self.loader.busy else f"{len(sessions)} saved"
        theme.blit_text(surf, status, (CONTENT.right - 6, TOOLBAR_Y + 12), 12, theme.DIM, anchor="midright")
        pygame.draw.line(surf, theme.BORDER, (0, SESSION_LIST.y - 2), (CONTENT.right, SESSION_LIST.y - 2))

        if self.loader.error:
            theme.blit_text(surf, theme.fit(self.loader.error, 13, CONTENT.w - 20), SESSION_LIST.center, 13, theme.RED, anchor="center")
            return
        entries = self.entries()
        if not entries:
            if not self.loader.busy:
                theme.blit_text(surf, "No finished sessions yet", SESSION_LIST.center, 15, theme.DIM, anchor="center")
            return

        uploads = self.app.backend.uploads
        self.scroll.clamp(len(entries), SESSION_LIST.h)
        clip = surf.get_clip()
        surf.set_clip(SESSION_LIST)
        for i, info in enumerate(entries):
            y = SESSION_LIST.y + i * SESSION_ROW_H - self.scroll.offset
            if y + SESSION_ROW_H < SESSION_LIST.y or y > SESSION_LIST.bottom:
                continue
            if i % 2:
                pygame.draw.rect(surf, theme.PANEL, (0, y, SESSION_LIST.w, SESSION_ROW_H))
            if info is None:
                theme.blit_text(surf, "All sessions", (8, y + 13), 15, theme.BLUE, bold=True, anchor="midleft")
                theme.blit_text(surf, f"{len(sessions)} sessions combined · each network once", (8, y + 32), 12, theme.DIM, anchor="midleft")
                continue
            theme.blit_text(surf, session_title(info), (8, y + 13), 15, theme.TEXT, bold=True, anchor="midleft")
            theme.blit_text(surf, fmt.short_duration(info.duration), (SESSION_LIST.right - 8, y + 13), 13, theme.DIM, anchor="midright")
            detail = f"Wi-Fi {fmt.count(info.wifi)} · BT {fmt.count(info.bt)} · open {fmt.count(info.open)}"
            theme.blit_text(surf, detail, (8, y + 32), 12, theme.DIM, anchor="midleft")
            if uploads.uploaded(info.name, "home"):
                theme.blit_text(surf, "✓ archived", (SESSION_LIST.right - 8, y + 32), 12, theme.GREEN, anchor="midright")
        surf.set_clip(clip)


class SessionNetsView(View):
    name = "session_nets"

    def __init__(self, app):
        super().__init__(app)
        self.loader = Loader()
        self.session: str | None = None
        self.title = ""
        self.filter_i = 0
        self.sort_i = 0
        self._rows_key = None
        self.list = DeviceList(DEVICE_LIST, lambda d: app.open_modal(DeviceModal(app, d)), signal_header="BEST")
        self.buttons = [
            Button((4, TOOLBAR_Y, 56, 24), "BACK", lambda: app.show("sessions"), size=12),
            Button((64, TOOLBAR_Y, 60, 24), lambda: FILTERS[self.filter_i][1], self._next_filter, size=12),
            Button((128, TOOLBAR_Y, 76, 24), lambda: SORTS[self.sort_i][1], self._next_sort, size=12),
            Button((208, TOOLBAR_Y, 48, 24), "MAP", self._show_on_map, size=12),
        ]

    def open(self, session: str | None, title: str) -> None:
        self.session, self.title = session, title
        self.filter_i = self.sort_i = 0
        self._rows_key = None
        self.list.set_rows([], reset_scroll=True)
        library = self.app.backend.sessions
        self.loader.start(lambda: library.devices(session))

    def _next_filter(self):
        self.filter_i = (self.filter_i + 1) % len(FILTERS)
        self.list.scroll.offset = 0

    def _next_sort(self):
        self.sort_i = (self.sort_i + 1) % len(SORTS)
        self.list.scroll.offset = 0

    def _show_on_map(self):
        self.app.views["map"].show_session(self.session, self.title)
        self.app.show("map")

    def rows(self):
        devices = self.loader.result
        key = (id(devices), self.filter_i, self.sort_i)
        if devices is not None and key != self._rows_key:
            phy = FILTERS[self.filter_i][0]
            shown = [d for d in devices if phy is None or d.phy == phy]
            self.list.set_rows(sort_devices(shown, SORTS[self.sort_i][0]))
            self._rows_key = key
        return self.list.rows

    def handle(self, ev: TouchEvent) -> bool:
        if super().handle(ev):
            return True
        self.rows()
        return self.list.handle(ev)

    def draw(self, surf: pygame.Surface, now: float) -> None:
        super().draw(surf, now)
        rows = self.rows()
        label = "loading…" if self.loader.busy else f"{len(rows):,} · {self.title}"
        theme.blit_text(surf, theme.fit(label, 12, CONTENT.right - 266), (CONTENT.right - 6, TOOLBAR_Y + 12), 12, theme.DIM, anchor="midright")
        if self.loader.error:
            empty = self.loader.error
        elif self.loader.busy:
            empty = "Loading session…"
        else:
            empty = "Nothing recorded in this session"
        self.list.draw(surf, empty)
