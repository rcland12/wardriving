"""NETS: live list of discovered Wi-Fi networks and Bluetooth devices."""

from __future__ import annotations

import time

import pygame

from .. import theme
from ..state import Capture, Device
from ..touch import TouchEvent
from ..widgets import Button
from . import CONTENT, View
from .devicelist import FILTERS, DeviceList, DeviceModal

__all__ = ["NetsView", "DeviceModal"]

LIST = pygame.Rect(CONTENT.x, CONTENT.y + 46, CONTENT.w, CONTENT.h - 46)
SORTS = [("new", "NEWEST"), ("signal", "SIGNAL")]


class NetsView(View):
    name = "nets"

    def __init__(self, app):
        super().__init__(app)
        self.filter_i = 0
        self.sort_i = 0
        self.list = DeviceList(LIST, lambda d: app.open_modal(DeviceModal(app, d)))
        self._cache_key = None
        self._cache_time = 0.0
        y = CONTENT.y + 3
        self.buttons = [
            Button((4, y, 74, 24), lambda: FILTERS[self.filter_i][1], self._next_filter, size=12),
            Button((82, y, 90, 24), lambda: SORTS[self.sort_i][1], self._next_sort, size=12),
        ]

    def _next_filter(self):
        self.filter_i = (self.filter_i + 1) % len(FILTERS)
        self.list.scroll.offset = 0

    def _next_sort(self):
        self.sort_i = (self.sort_i + 1) % len(SORTS)
        self.list.scroll.offset = 0

    def rows(self) -> list[Device]:
        st = self.state
        key = (st.version, self.filter_i, self.sort_i)
        now = time.monotonic()
        # Re-sort at most twice a second; signal values change constantly.
        if key != self._cache_key and now - self._cache_time > 0.5:
            self.list.set_rows(st.device_list(FILTERS[self.filter_i][0], SORTS[self.sort_i][0]))
            self._cache_key, self._cache_time = key, now
        return self.list.rows

    def handle(self, ev: TouchEvent) -> bool:
        if super().handle(ev):
            return True
        self.rows()
        return self.list.handle(ev)

    def draw(self, surf: pygame.Surface, now: float) -> None:
        super().draw(surf, now)
        rows = self.rows()
        theme.blit_text(surf, f"{len(rows):,} shown", (CONTENT.right - 6, CONTENT.y + 15), 12, theme.DIM, anchor="midright")
        empty = {
            Capture.IDLE: "Idle. Tap START to scan.",
            Capture.WAITING: "Waiting for GPS time…",
            Capture.STARTING: "Starting capture…",
            Capture.RUNNING: "Scanning… nothing heard yet",
            Capture.STOPPING: "Stopping…",
            Capture.ERROR: "Capture error. See LOG.",
        }[self.state.capture]
        self.list.draw(surf, empty)
