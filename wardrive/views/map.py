"""MAP: offline street map with network dots, live GPS position, and saved sessions."""

from __future__ import annotations

import logging
import math
import time
from pathlib import Path

import pygame

from .. import fmt, theme
from ..mapdata import MAX_ZOOM, MIN_ZOOM, MapFile, lonlat_to_world, meters_per_pixel, world_to_lonlat
from ..maprender import DotLayer, MapRenderer, Viewport
from ..touch import TouchEvent
from ..widgets import SLOP, Button
from . import CONTENT, View
from .devicelist import DeviceModal
from .sessions import Loader

log = logging.getLogger(__name__)

AREA = CONTENT
BTN = 38
LIVE_REFRESH = 1.0  # s between re-reading live devices
RECENTER_PX = 60  # while following GPS, re-render once the base image drifts this far


def open_map(directory: str) -> MapFile | None:
    try:
        files = sorted(Path(directory).glob("*.map"))
    except OSError:
        return None
    for path in files:
        try:
            return MapFile(path)
        except Exception as exc:  # corrupt or unsupported file: try the next one
            log.warning("cannot open map %s: %s", path, exc)
    return None


class MapView(View):
    name = "map"

    def __init__(self, app):
        super().__init__(app)
        self.map = open_map(app.cfg.map.dir)
        self.renderer = MapRenderer(self.map)
        lon, lat = self.map.center() if self.map else (-98.58, 39.83)
        cx, cy = lonlat_to_world(lon, lat)
        # Whole-region view until there's a GPS fix or saved data to centre on.
        self.vp = Viewport(cx, cy, 7, AREA.w, AREA.h)
        self.mode = "live"  # or "saved"
        self.session: str | None = None  # saved mode: a session name, or None for all
        self.session_title = "All sessions"
        self.follow = True
        self.dots = DotLayer()
        self.loader = Loader()
        self._loaded_result = None
        self._live_version, self._live_at = -1, 0.0
        self._base = pygame.Surface((AREA.w, AREA.h))
        self._base_vp: Viewport | None = None
        self._drag_start: tuple[int, int] | None = None
        self._drag_center: tuple[float, float] | None = None
        self._dragging = False
        self._centered_once = False
        self.last_render_ms = 0.0

        right = AREA.right - BTN - 6
        self.buttons = [
            Button((AREA.x + 6, AREA.y + 6, 70, 28), lambda: "LIVE" if self.mode == "live" else "SAVED", self.toggle_mode, size=12),
            Button((right, AREA.y + 6, BTN, BTN), "+", lambda: self.zoom_by(1), size=22),
            Button((right, AREA.y + 6 + BTN + 6, BTN, BTN), "−", lambda: self.zoom_by(-1), size=22),
            Button((right, AREA.y + 6 + 2 * (BTN + 6), BTN, BTN), "◎", self.recenter, size=18,
                   active=lambda: self.follow),
        ]

    # --- modes -------------------------------------------------------------------

    def show_session(self, session: str | None, title: str) -> None:
        """Open the map on a saved session (None = all sessions)."""
        self.mode, self.session, self.session_title = "saved", session, title
        self.follow = False
        self._centered_once = False
        self._start_load()

    def toggle_mode(self) -> None:
        if self.mode == "live":
            self.mode = "saved"
            self.follow = False
            self._start_load()
        else:
            self.mode = "live"
            self.follow = True
            self._live_version = -1
        self._centered_once = False

    def _start_load(self) -> None:
        library = self.app.backend.sessions
        session = self.session
        self.loader.start(lambda: library.devices(session))

    # --- view control ----------------------------------------------------------

    def zoom_by(self, delta: int) -> None:
        self.vp = self.vp.zoomed(delta)

    def recenter(self) -> None:
        g = self.state.gps
        if g.has_fix:
            self.follow = True
            self.vp.cx, self.vp.cy = lonlat_to_world(g.lon, g.lat)
            self.vp.zoom = max(self.vp.zoom, 14)
        elif self.dots.devices:
            self._center_on_dots()

    def _center_on_dots(self) -> None:
        devices = self.dots.devices
        if not devices:
            return
        lon = sum(d.lon for d in devices) / len(devices)
        lat = sum(d.lat for d in devices) / len(devices)
        self.vp.cx, self.vp.cy = lonlat_to_world(lon, lat)

    # --- input -------------------------------------------------------------------

    def handle(self, ev: TouchEvent) -> bool:
        if super().handle(ev):
            return True
        if ev.kind == "down":
            if not AREA.collidepoint(ev.pos):
                return False
            self._drag_start, self._drag_center, self._dragging = ev.pos, (self.vp.cx, self.vp.cy), False
            return True
        if self._drag_start is None:
            return False
        dx, dy = ev.pos[0] - self._drag_start[0], ev.pos[1] - self._drag_start[1]
        if ev.kind == "move":
            if not self._dragging and math.hypot(dx, dy) > SLOP:
                self._dragging = True
                self.follow = False
            if self._dragging:
                self.vp.cx = self._drag_center[0] - dx / self.vp.scale
                self.vp.cy = self._drag_center[1] - dy / self.vp.scale
            return True
        # up
        was_dragging, self._dragging, self._drag_start = self._dragging, False, None
        if not was_dragging:
            local = (ev.pos[0] - AREA.x, ev.pos[1] - AREA.y)
            device = self.dots.nearest(local)
            if device is not None:
                self.app.open_modal(DeviceModal(self.app, device))
        return True

    # --- data --------------------------------------------------------------------

    def _refresh_dots(self, now: float) -> None:
        st = self.state
        if self.mode == "live":
            if st.version != self._live_version and now - self._live_at >= LIVE_REFRESH:
                self.dots.set_devices(st.device_list(None, "new"))
                self._live_version, self._live_at = st.version, now
        elif self.loader.result is not self._loaded_result and not self.loader.busy:
            self._loaded_result = self.loader.result
            self.dots.set_devices(self.loader.result or [])
            if not self._centered_once and self.dots.devices:
                self._center_on_dots()
                self.vp.zoom = max(self.vp.zoom, 14)
                self._centered_once = True

    def update(self, now: float) -> None:
        super().update(now)
        g = self.state.gps
        if self.mode != "live" or not g.has_fix:
            return
        if not self._centered_once:  # first fix: jump from the region view to street level
            self.vp.cx, self.vp.cy = lonlat_to_world(g.lon, g.lat)
            self.vp.zoom = max(self.vp.zoom, self.app.cfg.map.default_zoom)
            self._centered_once = True
        elif self.follow:
            self.vp.cx, self.vp.cy = lonlat_to_world(g.lon, g.lat)

    # --- drawing -----------------------------------------------------------------

    def _base_offset(self) -> tuple[int, int] | None:
        """Pixel offset of the current view from the cached base image, or None if it's unusable."""
        b = self._base_vp
        if b is None or b.zoom != self.vp.zoom:
            return None
        return int(round((b.cx - self.vp.cx) * self.vp.scale)), int(round((b.cy - self.vp.cy) * self.vp.scale))

    def draw(self, surf: pygame.Surface, now: float) -> None:
        self._refresh_dots(now)
        offset = self._base_offset()
        stale = offset is None or (not self._dragging and (offset != (0, 0)) and (
            not self.follow or math.hypot(*offset) > RECENTER_PX))
        if stale:
            stats = self.renderer.render(self._base, self.vp)
            self._base_vp = Viewport(self.vp.cx, self.vp.cy, self.vp.zoom, self.vp.w, self.vp.h)
            self.last_render_ms = stats.ms
            offset = (0, 0)

        clip = surf.get_clip()
        surf.set_clip(AREA)
        surf.fill((19, 24, 31), AREA)
        surf.blit(self._base, (AREA.x + offset[0], AREA.y + offset[1]))
        # Dots and the GPS marker are drawn at their true position every frame.
        area_surf = surf.subsurface(AREA)
        self.dots.draw(area_surf, self.vp)
        self._draw_gps(area_surf)
        surf.set_clip(clip)
        self._draw_overlay(surf, now)
        super().draw(surf, now)

    def _draw_gps(self, surf: pygame.Surface) -> None:
        g = self.state.gps
        if not (g.lat or g.lon):
            return
        sx, sy = self.vp.to_screen(*lonlat_to_world(g.lon, g.lat))
        center = (int(sx), int(sy))
        if g.has_fix:
            if g.speed > 1:
                a = math.radians(g.track)
                tip = (sx + 13 * math.sin(a), sy - 13 * math.cos(a))
                left = (sx + 6 * math.sin(a - 2.4), sy - 6 * math.cos(a - 2.4))
                right = (sx + 6 * math.sin(a + 2.4), sy - 6 * math.cos(a + 2.4))
                pygame.draw.polygon(surf, theme.BLUE, [tip, left, right])
            pygame.draw.circle(surf, (255, 255, 255), center, 7)
            pygame.draw.circle(surf, theme.BLUE, center, 5)
        else:
            pygame.draw.circle(surf, theme.DIM, center, 6, 2)

    def _draw_overlay(self, surf: pygame.Surface, now: float) -> None:
        # Mode / status line
        if self.mode == "saved":
            status = "loading…" if self.loader.busy else f"{len(self.dots.devices):,} located · {self.session_title}"
        else:
            status = f"{len(self.dots.devices):,} located · live"
        if self.map is None:
            status = "No map installed · " + status
        tag = theme.text(theme.fit(status, 11, AREA.w - 140), 11, theme.TEXT)
        box = tag.get_rect(topleft=(AREA.x + 82, AREA.y + 13)).inflate(8, 4)
        pygame.draw.rect(surf, (0, 0, 0), box, border_radius=4)
        surf.blit(tag, (box.x + 4, box.y + 2))

        # Scale bar (bottom-left) and attribution (bottom-right)
        lon, lat = world_to_lonlat(self.vp.cx, self.vp.cy)
        mpp = meters_per_pixel(lat, self.vp.zoom)
        target = mpp * 80
        nice = min((v for v in _NICE_DISTANCES if v >= target * 0.5), default=_NICE_DISTANCES[-1])
        px = int(nice / mpp)
        x0, y0 = AREA.x + 8, AREA.bottom - 10
        pygame.draw.line(surf, theme.TEXT, (x0, y0), (x0 + px, y0), 2)
        label = fmt.distance(nice, self.app.cfg.units) if nice >= 1000 or self.app.cfg.units == "metric" else _feet(nice, self.app.cfg.units)
        theme.blit_text(surf, label, (x0, y0 - 3), 10, theme.TEXT, anchor="bottomleft")
        if self.map is not None:
            theme.blit_text(surf, self.map.attribution, (AREA.right - 4, AREA.bottom - 2), 9, theme.DIM, anchor="bottomright")
        theme.blit_text(surf, f"z{self.vp.zoom}", (AREA.right - BTN - 6 + BTN // 2, AREA.y + 6 + 3 * (BTN + 6) + 2), 10, theme.DIM, anchor="midtop")


_NICE_DISTANCES = [10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000, 100000]


def _feet(meters: float, units: str) -> str:
    return f"{meters:.0f} m" if units == "metric" else f"{meters * 3.28084:,.0f} ft"
