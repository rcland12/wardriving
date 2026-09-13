"""NETS: live list of discovered Wi-Fi networks and Bluetooth devices."""

from __future__ import annotations

import time
from datetime import datetime

import pygame

from .. import theme
from ..state import Capture, Device
from ..touch import TouchEvent
from ..widgets import Button, DragScroll
from . import CONTENT, Modal, View

ROW_H = 22
LIST = pygame.Rect(CONTENT.x, CONTENT.y + 46, CONTENT.w, CONTENT.h - 46)
FILTERS = [(None, "ALL"), ("wifi", "WIFI"), ("bt", "BT")]
SORTS = [("new", "NEWEST"), ("signal", "SIGNAL")]

# column x positions
X_NAME, X_CH_R, X_DBM_R, X_SEC = 6, 236, 276, 286


class NetsView(View):
    name = "nets"

    def __init__(self, app):
        super().__init__(app)
        self.filter_i = 0
        self.sort_i = 0
        self.scroll = DragScroll(LIST, ROW_H)
        self._cache_key = None
        self._rows: list[Device] = []
        self._cache_time = 0.0
        y = CONTENT.y + 3
        self.buttons = [
            Button((4, y, 74, 24), lambda: FILTERS[self.filter_i][1], self._next_filter, size=12),
            Button((82, y, 90, 24), lambda: SORTS[self.sort_i][1], self._next_sort, size=12),
        ]

    def _next_filter(self):
        self.filter_i = (self.filter_i + 1) % len(FILTERS)
        self.scroll.offset = 0

    def _next_sort(self):
        self.sort_i = (self.sort_i + 1) % len(SORTS)
        self.scroll.offset = 0

    def rows(self) -> list[Device]:
        st = self.state
        key = (st.version, self.filter_i, self.sort_i)
        now = time.monotonic()
        # Re-sort at most twice a second; signal values change constantly.
        if key != self._cache_key and now - self._cache_time > 0.5:
            self._rows = st.device_list(FILTERS[self.filter_i][0], SORTS[self.sort_i][0])
            self._cache_key, self._cache_time = key, now
        return self._rows

    def handle(self, ev: TouchEvent) -> bool:
        if super().handle(ev):
            return True
        consumed, row = self.scroll.handle(ev)
        if row is not None:
            rows = self.rows()
            if 0 <= row < len(rows):
                self.app.open_modal(DeviceModal(self.app, rows[row]))
        return consumed

    def draw(self, surf: pygame.Surface, now: float) -> None:
        super().draw(surf, now)
        rows = self.rows()
        st = self.state
        theme.blit_text(surf, f"{len(rows):,} shown", (CONTENT.right - 6, CONTENT.y + 15), 12, theme.DIM, anchor="midright")

        hy = CONTENT.y + 31
        theme.blit_text(surf, "NAME", (X_NAME, hy), 11, theme.DIM, bold=True)
        theme.blit_text(surf, "CH", (X_CH_R, hy), 11, theme.DIM, bold=True, anchor="topright")
        theme.blit_text(surf, "dBm", (X_DBM_R, hy), 11, theme.DIM, bold=True, anchor="topright")
        theme.blit_text(surf, "SEC", (X_SEC, hy), 11, theme.DIM, bold=True)
        pygame.draw.line(surf, theme.BORDER, (0, LIST.y - 2), (CONTENT.right, LIST.y - 2))

        if not rows:
            msg = {
                Capture.IDLE: "Idle. Tap START to scan.",
                Capture.WAITING: "Waiting for GPS time…",
                Capture.STARTING: "Starting capture…",
                Capture.RUNNING: "Scanning… nothing heard yet",
                Capture.STOPPING: "Stopping…",
                Capture.ERROR: "Capture error. See LOG.",
            }[st.capture]
            theme.blit_text(surf, msg, LIST.center, 15, theme.DIM, anchor="center")
            return

        self.scroll.clamp(len(rows), LIST.h)
        clip = surf.get_clip()
        surf.set_clip(LIST)
        mono_now = time.monotonic()
        first = self.scroll.offset // ROW_H
        for i in range(first, min(len(rows), first + LIST.h // ROW_H + 2)):
            d = rows[i]
            y = LIST.y + i * ROW_H - self.scroll.offset
            r = pygame.Rect(0, y, LIST.w, ROW_H)
            if d.new_until > mono_now:
                pygame.draw.rect(surf, theme.DARK_GREEN, r)
            elif i % 2:
                pygame.draw.rect(surf, theme.PANEL, r)
            cy = y + ROW_H // 2
            name_color = theme.TEXT if d.name else theme.DIM
            theme.blit_text(surf, theme.fit(d.label, 14, X_CH_R - 36 - X_NAME), (X_NAME, cy), 14, name_color, anchor="midleft")
            ch = d.channel if d.phy == "wifi" else "--"
            theme.blit_text(surf, ch, (X_CH_R, cy), 13, theme.DIM, mono=True, anchor="midright")
            sig = str(d.signal) if d.signal else "--"
            theme.blit_text(surf, sig, (X_DBM_R, cy), 13, _signal_color(d.signal), mono=True, anchor="midright")
            theme.blit_text(surf, d.crypt, (X_SEC, cy), 12, theme.CRYPT_COLORS.get(d.crypt, theme.TEXT), bold=True, anchor="midleft")
        surf.set_clip(clip)

        total_h = len(rows) * ROW_H
        if total_h > LIST.h:
            bar_h = max(16, LIST.h * LIST.h // total_h)
            bar_y = LIST.y + (LIST.h - bar_h) * self.scroll.offset // (total_h - LIST.h)
            pygame.draw.rect(surf, theme.BORDER, (CONTENT.right - 3, bar_y, 3, bar_h), border_radius=1)


def _signal_color(dbm: int):
    if dbm == 0:
        return theme.DIM
    if dbm >= -60:
        return theme.GREEN
    if dbm >= -75:
        return theme.AMBER
    return theme.RED


class DeviceModal(Modal):
    def __init__(self, app, device: Device):
        super().__init__(app)
        self.device = device
        self.title = device.label

    def lines(self):
        d = self.device
        first = datetime.fromtimestamp(d.first_seen).strftime("%H:%M:%S") if d.first_seen else "?"
        last = datetime.fromtimestamp(d.last_seen).strftime("%H:%M:%S") if d.last_seen else "?"
        loc = f"{d.lat:.5f}, {d.lon:.5f}" if d.lat or d.lon else "no fix"
        return [
            ("Type", "Wi-Fi AP" if d.phy == "wifi" else "Bluetooth", theme.TEXT),
            ("MAC", d.mac, theme.TEXT),
            ("Vendor", d.manuf or "Unknown", theme.TEXT),
            ("Security", d.crypt_raw or ("n/a" if d.phy == "bt" else d.crypt), theme.CRYPT_COLORS.get(d.crypt, theme.TEXT)),
            ("Channel", d.channel or "--", theme.TEXT),
            ("Signal", f"{d.signal} dBm" if d.signal else "--", _signal_color(d.signal)),
            ("Seen", f"{first} → {last}", theme.TEXT),
            ("Location", loc, theme.TEXT),
        ]
