"""STATS: session totals, security breakdown and capture source health."""

from __future__ import annotations

import pygame

from .. import fmt, theme
from ..state import Capture
from . import CONTENT, View

TILES = [
    ("wifi", "WI-FI", theme.TEXT),
    ("bt", "BLUETOOTH", theme.PURPLE),
    ("OPEN", "OPEN", theme.RED),
    ("WEP", "WEP", theme.AMBER),
    ("WPA2", "WPA/WPA2", theme.GREEN),
    ("WPA3", "WPA3", theme.BLUE),
]


class StatsView(View):
    name = "stats"

    def draw(self, surf: pygame.Surface, now: float) -> None:
        st = self.state
        c = st.counts()
        c["WPA2"] += c["WPA"]  # fold legacy WPA into one tile

        tw, th, gap = 118, 48, 4
        for i, (key, label, color) in enumerate(TILES):
            x = CONTENT.x + 4 + (i % 3) * (tw + gap)
            y = CONTENT.y + 4 + (i // 3) * (th + gap)
            r = pygame.Rect(x, y, tw, th)
            pygame.draw.rect(surf, theme.PANEL, r, border_radius=6)
            theme.blit_text(surf, fmt.count(c[key]), (r.centerx, r.y + 4), 22, color, bold=True, anchor="midtop")
            theme.blit_text(surf, label, (r.centerx, r.bottom - 4), 10, theme.DIM, bold=True, anchor="midbottom")

        u = self.app.cfg.units
        rows = [
            ("Duration", fmt.duration(st.session_seconds())),
            ("Distance", fmt.distance(st.distance_m, u)),
            ("Packets/s", f"{st.packets_per_sec:.0f}"),
            ("Kismet RAM", fmt.size(st.kismet_rss_kb * 1024) if st.kismet_rss_kb else "--"),
            ("Disk free", fmt.size(st.sys.disk_free_bytes) if st.sys.disk_free_bytes else "--"),
        ]
        y = CONTENT.y + 112
        for i, (label, value) in enumerate(rows):
            col_x = CONTENT.x + 8 + (i % 2) * 180
            row_y = y + (i // 2) * 20
            theme.blit_text(surf, label, (col_x, row_y), 12, theme.DIM)
            theme.blit_text(surf, value, (col_x + 172, row_y), 13, theme.TEXT, mono=True, anchor="topright")

        y = CONTENT.y + 178
        pygame.draw.line(surf, theme.BORDER, (6, y - 4), (CONTENT.right - 6, y - 4))
        theme.blit_text(surf, "SOURCES", (8, y), 11, theme.DIM, bold=True)
        y += 18
        if not st.sources:
            idle = st.capture in (Capture.IDLE, Capture.WAITING, Capture.ERROR)
            theme.blit_text(surf, "not capturing" if idle else "waiting for Kismet…", (8, y), 13, theme.DIM)
            return
        for s in st.sources:
            if s.error:
                dot, detail = theme.RED, s.error
            elif s.running:
                dot = theme.GREEN
                detail = "hopping" if s.hopping else (f"ch {s.channel}" if s.channel else "running")
                detail += f" · {s.packets:,} pkts"
            else:
                dot, detail = theme.AMBER, "not running"
            pygame.draw.circle(surf, dot, (14, y + 9), 5)
            theme.blit_text(surf, f"{s.name} ({s.interface})", (26, y + 1), 13, theme.TEXT, bold=True)
            theme.blit_text(surf, theme.fit(detail, 12, CONTENT.right - 160), (CONTENT.right - 6, y + 2), 12, theme.DIM, anchor="topright")
            y += 22
