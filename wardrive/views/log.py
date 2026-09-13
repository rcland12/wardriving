"""LOG: recent events from the app and Kismet, newest first."""

from __future__ import annotations

from datetime import datetime

import pygame

from .. import theme
from ..state import Event
from ..touch import TouchEvent
from ..widgets import DragScroll
from . import CONTENT, Modal, View

ROW_H = 22
LIST = pygame.Rect(CONTENT.x, CONTENT.y + 2, CONTENT.w, CONTENT.h - 4)


class LogView(View):
    name = "log"

    def __init__(self, app):
        super().__init__(app)
        self.scroll = DragScroll(LIST, ROW_H)

    def events(self) -> list[Event]:
        with self.state.lock:
            return list(reversed(self.state.events))

    def handle(self, ev: TouchEvent) -> bool:
        consumed, row = self.scroll.handle(ev)
        if row is not None:
            events = self.events()
            if 0 <= row < len(events):
                self.app.open_modal(EventModal(self.app, events[row]))
        return consumed

    def draw(self, surf: pygame.Surface, now: float) -> None:
        events = self.events()
        if not events:
            theme.blit_text(surf, "No events yet", LIST.center, 15, theme.DIM, anchor="center")
            return
        self.scroll.clamp(len(events), LIST.h)
        clip = surf.get_clip()
        surf.set_clip(LIST)
        first = self.scroll.offset // ROW_H
        for i in range(first, min(len(events), first + LIST.h // ROW_H + 2)):
            e = events[i]
            y = LIST.y + i * ROW_H - self.scroll.offset
            if i % 2:
                pygame.draw.rect(surf, theme.PANEL, (0, y, LIST.w, ROW_H))
            cy = y + ROW_H // 2
            ts = datetime.fromtimestamp(e.when).strftime("%H:%M:%S")
            theme.blit_text(surf, ts, (6, cy), 12, theme.DIM, mono=True, anchor="midleft")
            msg = theme.fit(e.text, 13, LIST.w - 80)
            theme.blit_text(surf, msg, (72, cy), 13, theme.LEVEL_COLORS.get(e.level, theme.TEXT), anchor="midleft")
        surf.set_clip(clip)


class EventModal(Modal):
    def __init__(self, app, event: Event):
        super().__init__(app)
        self.event = event
        self.title = datetime.fromtimestamp(event.when).strftime("Event at %H:%M:%S")

    def draw(self, surf, now):
        super().draw(surf, now)
        # Word-wrap the full message inside the panel.
        words, line, y = self.event.text.split(), "", self.rect.y + 48
        width = self.rect.w - 32
        color = theme.LEVEL_COLORS.get(self.event.level, theme.TEXT)
        for word in words:
            trial = f"{line} {word}".strip()
            if theme.font(14).size(trial)[0] > width and line:
                theme.blit_text(surf, line, (self.rect.x + 16, y), 14, color)
                y += 20
                line = word
            else:
                line = trial
            if y > self.rect.bottom - 40:
                break
        if line:
            theme.blit_text(surf, line, (self.rect.x + 16, y), 14, color)
