"""GPS: fix quality, position, and a satellite signal chart that doubles as a GPS test."""

from __future__ import annotations

import time

import pygame

from .. import fmt, theme
from . import CONTENT, View

SNR_GOOD, SNR_MAX = 30.0, 50.0  # dB-Hz: >30 is a solid signal, ~50 is excellent


def fix_label(gps) -> tuple[str, tuple]:
    if not gps.gpsd_up:
        return "GPSD DOWN", theme.RED
    if not gps.device_present:
        return "NO GPS", theme.RED
    if gps.mode >= 3:
        return "3D FIX", theme.GREEN
    if gps.mode == 2:
        return "2D FIX", theme.AMBER
    return "NO FIX", theme.RED


def diagnosis(gps) -> tuple[str, tuple]:
    """One plain-language line explaining the current GPS state."""
    if not gps.gpsd_up:
        return "gpsd is not running (systemctl status gpsd)", theme.RED
    if not gps.device_present:
        return "No data from receiver: check the USB GPS is plugged in", theme.RED
    if gps.has_fix:
        return "Receiver OK", theme.GREEN
    s = int(time.monotonic() - gps.searching_since) if gps.searching_since else 0
    searching = f"{s // 60}:{s % 60:02d}"
    if gps.sats_seen == 0:
        return f"Receiver OK · no satellites · {searching} · needs open sky", theme.AMBER
    if not gps.time_valid:
        return f"Cold start · {gps.sats_seen} heard · {searching} · keep sky view", theme.AMBER
    return f"Acquiring · {gps.sats_seen} heard, {gps.sats_used} used · {searching}", theme.AMBER


class GpsView(View):
    name = "gps"

    def draw(self, surf: pygame.Surface, now: float) -> None:
        g = self.state.gps
        u = self.app.cfg.units
        label, color = fix_label(g)

        top = pygame.Rect(CONTENT.x + 4, CONTENT.y + 4, CONTENT.w - 8, 50)
        pygame.draw.rect(surf, theme.PANEL, top, border_radius=6)
        theme.blit_text(surf, label, (top.x + 10, top.centery), 24, color, bold=True, anchor="midleft")
        theme.blit_text(surf, f"{g.sats_used}/{g.sats_seen} sats", (top.right - 10, top.y + 7), 15, theme.TEXT, anchor="topright")
        hdop = f"HDOP {g.hdop:.1f}" if g.hdop else "HDOP --"
        theme.blit_text(surf, hdop, (top.right - 10, top.bottom - 6), 12, theme.DIM, anchor="bottomright")

        has_pos = g.has_fix
        y = top.bottom + 6
        for name, value in (("LAT", f"{g.lat:+.6f}" if has_pos else "--"), ("LON", f"{g.lon:+.6f}" if has_pos else "--")):
            theme.blit_text(surf, name, (CONTENT.x + 10, y + 3), 11, theme.DIM, bold=True)
            theme.blit_text(surf, value, (CONTENT.x + 48, y), 18, theme.TEXT, mono=True)
            y += 23

        rows = [
            ("Altitude", fmt.altitude(g.alt, u) if g.mode >= 3 and has_pos else "--"),
            ("Speed", fmt.speed(g.speed, u) if has_pos else "--"),
            ("Heading", f"{g.track:.0f}° {fmt.cardinal(g.track)}" if has_pos and g.speed > 1 else "--"),
            ("GPS time", fmt.local_time(g.time) if g.time_valid else "--"),
        ]
        for i, (name, value) in enumerate(rows):
            col_x = CONTENT.x + 10 + (i % 2) * 180
            row_y = y + 2 + (i // 2) * 20
            theme.blit_text(surf, name, (col_x, row_y + 1), 12, theme.DIM)
            theme.blit_text(surf, value, (col_x + 170, row_y), 14, theme.TEXT, anchor="topright")

        self._draw_satellites(surf, g, pygame.Rect(CONTENT.x + 4, y + 46, CONTENT.w - 8, CONTENT.bottom - (y + 46) - 4))

    def _draw_satellites(self, surf: pygame.Surface, g, area: pygame.Rect) -> None:
        pygame.draw.rect(surf, theme.PANEL, area, border_radius=6)
        text, color = diagnosis(g)
        theme.blit_text(surf, theme.fit(text, 12, area.w - 12), (area.x + 6, area.y + 4), 12, color)

        chart = pygame.Rect(area.x + 6, area.y + 22, area.w - 12, area.h - 40)
        sats = g.satellites[:24]
        if not sats:
            theme.blit_text(surf, "no satellite signals", chart.center, 13, theme.BORDER, anchor="center")
            return
        slot = chart.w / max(len(sats), 12)
        bar_w = max(4, int(slot) - 3)
        pygame.draw.line(surf, theme.BORDER, (chart.x, chart.bottom), (chart.right, chart.bottom))
        good_y = chart.bottom - int(chart.h * SNR_GOOD / SNR_MAX)
        pygame.draw.line(surf, theme.PANEL_HI, (chart.x, good_y), (chart.right, good_y))
        for i, (prn, snr, used) in enumerate(sats):
            h = int(chart.h * min(snr, SNR_MAX) / SNR_MAX)
            x = chart.x + int(i * slot)
            bar_color = theme.GREEN if used else (theme.AMBER if snr >= SNR_GOOD else theme.DIM)
            if h:
                pygame.draw.rect(surf, bar_color, (x, chart.bottom - h, bar_w, h))
            theme.blit_text(surf, str(prn), (x + bar_w // 2, chart.bottom + 2), 9, theme.DIM, anchor="midtop")
