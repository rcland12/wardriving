"""Scrollable Wi-Fi/Bluetooth device list and detail popup, shared by the live
NETS view and the saved-session browser."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Callable

import pygame

from .. import fmt, theme
from ..state import Device
from ..touch import TouchEvent
from ..widgets import DragScroll
from . import Modal

ROW_H = 22
FILTERS = [(None, "ALL"), ("wifi", "WIFI"), ("bt", "BT")]

# column x positions (relative to the list's left edge)
X_NAME, X_CH_R, X_DBM_R, X_SEC = 6, 236, 276, 286


def signal_color(dbm: int):
    if dbm == 0:
        return theme.DIM
    if dbm >= -60:
        return theme.GREEN
    if dbm >= -75:
        return theme.AMBER
    return theme.RED


class DeviceList:
    """Draws `rows` inside `rect` with a column header above it; tap a row to select."""

    def __init__(self, rect: pygame.Rect, on_select: Callable[[Device], None], signal_header: str = "dBm"):
        self.rect = rect
        self.on_select = on_select
        self.signal_header = signal_header
        self.scroll = DragScroll(rect, ROW_H)
        self.rows: list[Device] = []

    def set_rows(self, rows: list[Device], reset_scroll: bool = False) -> None:
        self.rows = rows
        if reset_scroll:
            self.scroll.offset = 0

    def handle(self, ev: TouchEvent) -> bool:
        consumed, row = self.scroll.handle(ev)
        if row is not None and 0 <= row < len(self.rows):
            self.on_select(self.rows[row])
        return consumed

    def draw(self, surf: pygame.Surface, empty_message: str = "") -> None:
        r = self.rect
        hy = r.y - 15
        theme.blit_text(surf, "NAME", (r.x + X_NAME, hy), 11, theme.DIM, bold=True)
        theme.blit_text(surf, "CH", (r.x + X_CH_R, hy), 11, theme.DIM, bold=True, anchor="topright")
        theme.blit_text(surf, self.signal_header, (r.x + X_DBM_R, hy), 11, theme.DIM, bold=True, anchor="topright")
        theme.blit_text(surf, "SEC", (r.x + X_SEC, hy), 11, theme.DIM, bold=True)
        pygame.draw.line(surf, theme.BORDER, (r.x, r.y - 2), (r.right, r.y - 2))

        rows = self.rows
        if not rows:
            if empty_message:
                theme.blit_text(surf, empty_message, r.center, 15, theme.DIM, anchor="center")
            return

        self.scroll.clamp(len(rows), r.h)
        clip = surf.get_clip()
        surf.set_clip(r)
        now = time.monotonic()
        first = self.scroll.offset // ROW_H
        for i in range(first, min(len(rows), first + r.h // ROW_H + 2)):
            d = rows[i]
            y = r.y + i * ROW_H - self.scroll.offset
            row_rect = pygame.Rect(r.x, y, r.w, ROW_H)
            if d.new_until > now:
                pygame.draw.rect(surf, theme.DARK_GREEN, row_rect)
            elif i % 2:
                pygame.draw.rect(surf, theme.PANEL, row_rect)
            cy = y + ROW_H // 2
            name_color = theme.TEXT if d.name else theme.DIM
            theme.blit_text(surf, theme.fit(d.label, 14, X_CH_R - 36 - X_NAME), (r.x + X_NAME, cy), 14, name_color, anchor="midleft")
            ch = d.channel if d.phy == "wifi" else "--"
            theme.blit_text(surf, ch, (r.x + X_CH_R, cy), 13, theme.DIM, mono=True, anchor="midright")
            sig = str(d.signal) if d.signal else "--"
            theme.blit_text(surf, sig, (r.x + X_DBM_R, cy), 13, signal_color(d.signal), mono=True, anchor="midright")
            theme.blit_text(surf, d.crypt, (r.x + X_SEC, cy), 12, theme.CRYPT_COLORS.get(d.crypt, theme.TEXT), bold=True, anchor="midleft")
        surf.set_clip(clip)

        total_h = len(rows) * ROW_H
        if total_h > r.h:
            bar_h = max(16, r.h * r.h // total_h)
            bar_y = r.y + (r.h - bar_h) * self.scroll.offset // (total_h - r.h)
            pygame.draw.rect(surf, theme.BORDER, (r.right - 3, bar_y, 3, bar_h), border_radius=1)


class DeviceModal(Modal):
    """Details for one device. Saved-session devices carry extra fields."""

    def __init__(self, app, device: Device):
        super().__init__(app)
        self.device = device
        self.title = device.label

    def lines(self):
        d = self.device
        saved = hasattr(d, "packets")
        time_format = "%m/%d %H:%M" if saved else "%H:%M:%S"
        first = datetime.fromtimestamp(d.first_seen).strftime(time_format) if d.first_seen else "?"
        last = datetime.fromtimestamp(d.last_seen).strftime(time_format) if d.last_seen else "?"
        loc = f"{d.lat:.5f}, {d.lon:.5f}" if d.lat or d.lon else "no fix"
        kind = getattr(d, "dev_type", "") or ("Wi-Fi AP" if d.phy == "wifi" else "Bluetooth")
        security = d.crypt_raw or ("n/a" if d.phy == "bt" else d.crypt)
        if getattr(d, "wps", False):
            security += " · WPS"
        lines = [
            ("Type", kind, theme.TEXT),
            ("MAC", d.mac, theme.TEXT),
            ("Vendor", d.manuf or "Unknown", theme.TEXT),
            ("Security", security, theme.CRYPT_COLORS.get(d.crypt, theme.TEXT)),
            ("Channel", _channel(d), theme.TEXT),
            ("Strongest" if saved else "Signal", f"{d.signal} dBm" if d.signal else "--", signal_color(d.signal)),
            ("Seen", f"{first} → {last}", theme.TEXT),
            ("Location", loc, theme.TEXT),
        ]
        if saved:
            extra = f"{fmt.count(d.packets)} packets"
            if d.sessions > 1:
                extra += f" · {d.sessions} sessions"
            lines.insert(7, ("Heard", extra, theme.DIM))
        return lines


def _channel(d: Device) -> str:
    if d.phy != "wifi":
        return d.channel or "--"
    freq = getattr(d, "frequency", 0)
    return f"{d.channel} ({freq / 1e6:.3f} GHz)" if freq else (d.channel or "--")
