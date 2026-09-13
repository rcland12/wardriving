"""Draw the offline vector map (and network dots) onto a pygame surface."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import numpy as np
import pygame

from . import theme
from .mapdata import EXTENT, LOD_TILE_ZOOM, MAX_ZOOM, MIN_ZOOM, TILE_SIZE, MapFile, area_lod, place_kinds_for, road_lod

# Dark palette, matching the UI.
LAND = (19, 24, 31)
PARK = (26, 46, 33)
WATER = (22, 48, 72)
WATER_LINE = (40, 80, 115)
BUILDING = (42, 48, 57)
BUILDING_EDGE = (58, 66, 77)
ROAD_COLOR = [(214, 150, 60), (200, 140, 62), (190, 160, 90), (160, 160, 110), (140, 146, 155), (110, 117, 128), (84, 91, 101), (68, 74, 83)]
ROAD_WIDTH_Z17 = [10, 9, 8, 7, 6, 5, 3, 1]  # px at zoom 17, halved per zoom level out
# First zoom each road class is drawn at: keeps region views to the roads you can read.
ROAD_MIN_ZOOM = [0, 8, 9, 11, 12, 13, 15, 16]
WATERWAY_MIN_ZOOM = [10, 13, 16]  # river, stream/canal, drain/ditch
# Printed-map abbreviations for street labels (display only; the data keeps full names).
STREET_WORDS = {
    "street": "St", "avenue": "Ave", "boulevard": "Blvd", "road": "Rd", "drive": "Dr", "lane": "Ln",
    "court": "Ct", "place": "Pl", "parkway": "Pkwy", "highway": "Hwy", "circle": "Cir", "trail": "Trl",
    "terrace": "Ter", "expressway": "Expy", "freeway": "Fwy", "square": "Sq", "crossing": "Xing",
    "international": "Intl", "memorial": "Mem", "national": "Natl", "saint": "St", "mount": "Mt",
    "northeast": "NE", "northwest": "NW", "southeast": "SE", "southwest": "SW",
    "north": "N", "south": "S", "east": "E", "west": "W",
}
PLACE_STYLE = [(17, True, theme.TEXT), (15, True, theme.TEXT), (13, True, (200, 205, 212)), (12, False, (190, 196, 204)),
               (11, False, theme.DIM), (11, False, theme.DIM)]


@dataclass
class Viewport:
    cx: float  # map center, normalized Web Mercator
    cy: float
    zoom: int
    w: int
    h: int

    @property
    def scale(self) -> float:
        """Screen pixels per normalized world unit."""
        return TILE_SIZE * 2**self.zoom

    def to_screen(self, x, y):
        s = self.scale
        return (x - self.cx) * s + self.w / 2, (y - self.cy) * s + self.h / 2

    def to_world(self, sx: float, sy: float) -> tuple[float, float]:
        s = self.scale
        return self.cx + (sx - self.w / 2) / s, self.cy + (sy - self.h / 2) / s

    def world_bounds(self, margin_px: float = 0) -> tuple[float, float, float, float]:
        x0, y0 = self.to_world(-margin_px, -margin_px)
        x1, y1 = self.to_world(self.w + margin_px, self.h + margin_px)
        return x0, y0, x1, y1

    def zoomed(self, delta: int) -> "Viewport":
        return Viewport(self.cx, self.cy, max(MIN_ZOOM, min(MAX_ZOOM, self.zoom + delta)), self.w, self.h)


@dataclass
class RenderStats:
    tiles: int = 0
    polygons: int = 0
    lines: int = 0
    labels: int = 0
    ms: float = 0.0
    extra: dict = field(default_factory=dict)


@dataclass
class _Layer:
    recs: list
    arrays: list  # (n, 2) float32 coordinates per feature, in tile units
    bbox: np.ndarray  # (features, 4): min x, min y, max x, max y


_EMPTY = _Layer([], [], np.zeros((0, 4), dtype=np.float32))


def _layer(tile: dict, layer: str, coord_index: int) -> _Layer:
    """Per-tile cache of a layer's coordinates and bounding boxes (built on first use)."""
    key = "_np_" + layer
    cached = tile.get(key)
    if cached is None:
        recs = tile.get(layer, ())
        if not recs:
            cached = _EMPTY
        else:
            arrays = [np.asarray(rec[coord_index], dtype=np.float32).reshape(-1, 2) for rec in recs]
            starts = np.cumsum([0] + [len(a) for a in arrays[:-1]])
            flat = np.concatenate(arrays)
            bbox = np.hstack([np.minimum.reduceat(flat, starts), np.maximum.reduceat(flat, starts)])
            cached = _Layer(list(recs), arrays, bbox)
        tile[key] = cached
    return cached


class MapRenderer:
    def __init__(self, mapfile: MapFile | None):
        self.map = mapfile
        self._rotated: dict[tuple, pygame.Surface] = {}

    # --- helpers -----------------------------------------------------------------

    def _tile_transform(self, vp: Viewport, lod: int, tx: int, ty: int) -> tuple[float, float, float]:
        z = LOD_TILE_ZOOM[lod]
        k = vp.scale / (2**z * EXTENT)
        ox = tx * EXTENT * k - vp.cx * vp.scale + vp.w / 2
        oy = ty * EXTENT * k - vp.cy * vp.scale + vp.h / 2
        return k, ox, oy

    def _candidates(self, layer: _Layer, vp: Viewport, k: float, ox: float, oy: float, pad: float = 2) -> np.ndarray:
        """Indexes of features whose bounding box intersects the view (one vectorized test per tile)."""
        if not len(layer.recs):
            return np.zeros(0, dtype=int)
        x0, y0 = (-pad - ox) / k, (-pad - oy) / k
        x1, y1 = (vp.w + pad - ox) / k, (vp.h + pad - oy) / k
        b = layer.bbox
        return np.nonzero((b[:, 2] >= x0) & (b[:, 0] <= x1) & (b[:, 3] >= y0) & (b[:, 1] <= y1))[0]

    def _text_along(self, text: str, size: int, color, angle: float) -> pygame.Surface:
        bucket = int(round(angle / 5.0)) * 5  # cache rotated labels in 5° steps
        key = (text, size, color, bucket)
        surf = self._rotated.get(key)
        if surf is None:
            if len(self._rotated) > 800:
                self._rotated.clear()
            surf = pygame.transform.rotate(theme.text(text, size, color), bucket)
            self._rotated[key] = surf
        return surf

    def _fill(self, surf: pygame.Surface, vp: Viewport, pts: np.ndarray, color, edge=None) -> bool:
        mn, mx = pts.min(axis=0), pts.max(axis=0)
        if mx[0] - mn[0] > 2 * vp.w or mx[1] - mn[1] > 2 * vp.h:
            pts = _clip_polygon(pts, -4, -4, vp.w + 4, vp.h + 4)
            if len(pts) < 3:
                return False
        pl = pts.tolist()
        pygame.draw.polygon(surf, color, pl)
        if edge is not None:
            pygame.draw.polygon(surf, edge, pl, 1)
        return True

    # --- layers ------------------------------------------------------------------

    def render(self, surf: pygame.Surface, vp: Viewport) -> RenderStats:
        started = time.perf_counter()
        stats = RenderStats()
        surf.fill(LAND)
        if self.map is None:
            self._grid(surf, vp)
            stats.ms = (time.perf_counter() - started) * 1000
            return stats
        bounds = vp.world_bounds(margin_px=40)
        z = vp.zoom
        placed: list[pygame.Rect] = []

        area_lods = [area_lod(z)] + ([3] if z >= 15 else [])
        # Tiles store features clipped to their own area, so every piece is drawn (no de-duplication).
        for layer, color in (("p", PARK), ("w", WATER)):
            for lod in area_lods:
                for tx, ty, tile in self.map.tiles_in(lod, *bounds):
                    stats.tiles += 1
                    k, ox, oy = self._tile_transform(vp, lod, tx, ty)
                    data = _layer(tile, layer, 1)
                    for i in self._candidates(data, vp, k, ox, oy):
                        b = data.bbox[i]
                        if (b[2] - b[0] + b[3] - b[1]) * k <= 2:
                            continue  # smaller than a couple of pixels
                        stats.polygons += self._fill(surf, vp, data.arrays[i] * k + (ox, oy), color)

        lines_lod = max(1, road_lod(z))
        for tx, ty, tile in (self.map.tiles_in(lines_lod, *bounds) if z >= WATERWAY_MIN_ZOOM[0] else ()):
            k, ox, oy = self._tile_transform(vp, lines_lod, tx, ty)
            data = _layer(tile, "l", 3)
            for i in self._candidates(data, vp, k, ox, oy):
                rec = data.recs[i]
                if z < WATERWAY_MIN_ZOOM[rec[1]]:
                    continue
                width = 3 if rec[1] == 0 and z >= 13 else 1
                pygame.draw.lines(surf, WATER_LINE, False, (data.arrays[i] * k + (ox, oy)).tolist(), width)
                stats.lines += 1

        if z >= 16:
            for tx, ty, tile in self.map.tiles_in(3, *bounds):
                k, ox, oy = self._tile_transform(vp, 3, tx, ty)
                data = _layer(tile, "b", 1)
                for i in self._candidates(data, vp, k, ox, oy):
                    stats.polygons += self._fill(surf, vp, data.arrays[i] * k + (ox, oy), BUILDING, BUILDING_EDGE if z >= 17 else None)

        lod = road_lod(z)
        roads: list[tuple[int, list, str]] = []
        for tx, ty, tile in self.map.tiles_in(lod, *bounds):
            k, ox, oy = self._tile_transform(vp, lod, tx, ty)
            names = tile.get("n", [])
            data = _layer(tile, "r", 3)
            for i in self._candidates(data, vp, k, ox, oy, pad=10):
                cls, name_i = data.recs[i][1], data.recs[i][2]
                if z < ROAD_MIN_ZOOM[cls]:
                    continue
                roads.append((cls, data.arrays[i] * k + (ox, oy), names[name_i] if name_i >= 0 else ""))
        roads.sort(key=lambda r: -r[0])  # minor roads first, motorways on top
        for cls, pts, _ in roads:
            width = max(1, min(18, int(round(ROAD_WIDTH_Z17[cls] * 2 ** (z - 17)))))
            if cls <= 1 and z >= 9:
                width = max(width, 2)
            pygame.draw.lines(surf, ROAD_COLOR[cls], False, pts.tolist(), width)
            stats.lines += 1

        stats.labels += self._place_labels(surf, vp, bounds, placed)
        if z >= 16:
            stats.labels += self._street_labels(surf, vp, roads, placed)
        if z >= 17:
            stats.labels += self._points(surf, vp, bounds, "i", placed, z)
        if z >= 18:
            stats.labels += self._points(surf, vp, bounds, "a", placed, z)

        stats.ms = (time.perf_counter() - started) * 1000
        return stats

    def _place_labels(self, surf, vp, bounds, placed) -> int:
        z, count, limit = vp.zoom, 0, place_kinds_for(vp.zoom)
        lod = road_lod(z)
        candidates = []
        for tx, ty, tile in self.map.tiles_in(lod, *bounds):
            k, ox, oy = self._tile_transform(vp, lod, tx, ty)
            names = tile.get("n", [])
            # Each tile lists its places most important first; interleave tiles by that rank.
            for rank, (kind, name_i, x, y) in enumerate(tile.get("t", ())):
                if kind < limit and name_i >= 0:
                    candidates.append((kind, rank, names[name_i], x * k + ox, y * k + oy))
        for kind, _, name, sx, sy in sorted(candidates, key=lambda c: c[:2]):
            size, bold, color = PLACE_STYLE[kind]
            label = theme.text(name, size, color, bold=bold)
            rect = label.get_rect(center=(sx, sy))
            if rect.right < 0 or rect.bottom < 0 or rect.left > vp.w or rect.top > vp.h or rect.inflate(8, 4).collidelist(placed) >= 0:
                continue
            shadow = theme.text(name, size, LAND, bold=bold)
            surf.blit(shadow, rect.move(1, 1))
            surf.blit(label, rect)
            placed.append(rect)
            count += 1
        return count

    def _street_labels(self, surf, vp, roads, placed) -> int:
        count, done = 0, {}
        size = 11 if vp.zoom < 18 else 12
        for cls, pts, name in sorted(roads, key=lambda r: r[0]):
            if not name or cls >= 7:
                continue
            name = abbreviate(name)
            best = _longest_visible_segment(_straight_runs(pts), vp.w, vp.h)
            if best is None:
                continue
            a, b, length = best
            label_w = theme.font(size).size(name)[0]
            if length < label_w + 12:
                continue
            mid = (a + b) / 2
            prev = done.get(name)
            if prev is not None and math.dist(prev, mid) < 220:
                continue
            angle = -math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
            if angle > 90:
                angle -= 180
            elif angle < -90:
                angle += 180
            label = self._text_along(name, size, (225, 229, 234), angle)
            rect = label.get_rect(center=(int(mid[0]), int(mid[1])))
            if rect.collidelist(placed) >= 0:
                continue
            surf.blit(label, rect)
            placed.append(rect.inflate(-rect.w // 4, -rect.h // 4))
            done[name] = mid
            count += 1
        return count

    def _points(self, surf, vp, bounds, layer: str, placed, z: int) -> int:
        count = 0
        for tx, ty, tile in self.map.tiles_in(3, *bounds):
            k, ox, oy = self._tile_transform(vp, 3, tx, ty)
            names = tile.get("n", [])
            for rec in tile.get(layer, ()):
                if layer == "a":
                    name_i, x, y = rec
                    size, color = 9, (150, 157, 168)
                else:
                    _, name_i, x, y = rec
                    size, color = 10, (230, 200, 120)
                sx, sy = x * k + ox, y * k + oy
                if not (0 <= sx <= vp.w and 0 <= sy <= vp.h) or name_i < 0:
                    continue
                label = theme.text(names[name_i], size, color)
                rect = label.get_rect(center=(int(sx), int(sy) - (7 if layer == "i" else 0)))
                if rect.collidelist(placed) >= 0:
                    continue
                if layer == "i":
                    pygame.draw.circle(surf, color, (int(sx), int(sy)), 2)
                surf.blit(label, rect)
                placed.append(rect)
                count += 1
        return count

    def _grid(self, surf, vp) -> None:
        """No map installed: a faint grid so panning still has a reference."""
        step = TILE_SIZE
        x0, y0 = vp.to_screen(math.floor(vp.cx * 2**vp.zoom) / 2**vp.zoom, math.floor(vp.cy * 2**vp.zoom) / 2**vp.zoom)
        for gx in range(int(x0 % step) - step, vp.w + step, step):
            pygame.draw.line(surf, (30, 36, 44), (gx, 0), (gx, vp.h))
        for gy in range(int(y0 % step) - step, vp.h + step, step):
            pygame.draw.line(surf, (30, 36, 44), (0, gy), (vp.w, gy))


_abbrev_cache: dict[str, str] = {}


def abbreviate(name: str) -> str:
    short = _abbrev_cache.get(name)
    if short is None:
        words = name.split()
        # Keep a lone word ("North") or a leading directional that is really the name ("East Point").
        short = " ".join(
            STREET_WORDS.get(w.lower(), w) if len(words) > 1 and not (i == 0 and w.lower() in ("north", "south", "east", "west") and len(words) == 2) else w
            for i, w in enumerate(words)
        )
        if len(_abbrev_cache) > 5000:
            _abbrev_cache.clear()
        _abbrev_cache[name] = short
    return short


def _clip_polygon(pts: np.ndarray, x0: float, y0: float, x1: float, y1: float) -> np.ndarray:
    """Sutherland-Hodgman clip of a closed ring to a rectangle (vectorized per edge).

    Filling a huge polygon whose vertices lie far off-screen is slow in pygame; clipping
    first keeps the work proportional to what's visible."""
    for axis, bound, keep_above in ((0, x0, True), (0, x1, False), (1, y0, True), (1, y1, False)):
        if len(pts) < 3:
            return pts[:0]
        nxt = np.roll(pts, -1, axis=0)
        inside = pts[:, axis] >= bound if keep_above else pts[:, axis] <= bound
        crosses = inside != np.roll(inside, -1)
        denom = nxt[:, axis] - pts[:, axis]
        t = np.divide(bound - pts[:, axis], denom, out=np.zeros(len(pts), dtype=pts.dtype), where=denom != 0)
        out = np.empty((len(pts) * 2, 2), dtype=pts.dtype)
        out[0::2] = pts
        out[1::2] = pts + (nxt - pts) * t[:, None]
        keep = np.empty(len(pts) * 2, dtype=bool)
        keep[0::2], keep[1::2] = inside, crosses
        pts = out[keep]
    return pts


def _straight_runs(pts: np.ndarray, max_turn_deg: float = 15.0) -> np.ndarray:
    """Drop vertices where the line barely turns, so a gently curving street made of many
    short segments becomes a few long ones that a label can fit along."""
    if len(pts) <= 2:
        return pts
    d = np.diff(pts, axis=0)
    angles = np.arctan2(d[:, 1], d[:, 0])
    turn = np.abs((np.diff(angles) + np.pi) % (2 * np.pi) - np.pi)
    keep = np.concatenate([[True], turn > math.radians(max_turn_deg), [True]])
    return pts[keep]


def _longest_visible_segment(pts: np.ndarray, w: int, h: int, margin: int = 6):
    """Clip each segment of a polyline to the view; return (start, end, length) of the longest."""
    best = None
    lo_x, lo_y, hi_x, hi_y = margin, margin, w - margin, h - margin
    for p, q in zip(pts[:-1], pts[1:]):
        # Liang-Barsky clipping of p->q against the view rectangle.
        dx, dy = q[0] - p[0], q[1] - p[1]
        t0, t1 = 0.0, 1.0
        ok = True
        for edge_p, edge_q in ((-dx, p[0] - lo_x), (dx, hi_x - p[0]), (-dy, p[1] - lo_y), (dy, hi_y - p[1])):
            if edge_p == 0:
                if edge_q < 0:
                    ok = False
                    break
                continue
            t = edge_q / edge_p
            if edge_p < 0:
                t0 = max(t0, t)
            else:
                t1 = min(t1, t)
            if t0 > t1:
                ok = False
                break
        if not ok:
            continue
        a = np.array([p[0] + t0 * dx, p[1] + t0 * dy])
        b = np.array([p[0] + t1 * dx, p[1] + t1 * dy])
        length = float(np.hypot(*(b - a)))
        if best is None or length > best[2]:
            best = (a, b, length)
    return best


# --- network dots ------------------------------------------------------------------------


# Each security type has a shape as well as a color, so dots can be told apart without
# relying on color vision.
MARKER_SHAPES = {"OPEN": "triangle", "WEP": "diamond", "WPA": "diamond", "WPA2": "circle", "WPA3": "square", "BT": "plus"}
MARKER_LEGEND = [("OPEN", "Open"), ("WEP", "WEP / WPA"), ("WPA2", "WPA2"), ("WPA3", "WPA3"), ("BT", "Bluetooth")]
OUTLINE = (0, 0, 0)


def draw_marker(surf: pygame.Surface, crypt: str, center: tuple[int, int], r: int) -> None:
    """One network marker: shape and color by security type, outlined in black."""
    color = theme.CRYPT_COLORS.get(crypt, theme.TEXT)
    shape = MARKER_SHAPES.get(crypt, "circle")
    x, y = center
    if shape == "circle":
        pygame.draw.circle(surf, OUTLINE, center, r + 1)
        pygame.draw.circle(surf, color, center, r)
    elif shape == "square":
        k = r - 1
        pygame.draw.rect(surf, OUTLINE, (x - k - 1, y - k - 1, 2 * k + 3, 2 * k + 3))
        pygame.draw.rect(surf, color, (x - k, y - k, 2 * k + 1, 2 * k + 1))
    elif shape == "diamond":
        k = r + 1
        pygame.draw.polygon(surf, OUTLINE, [(x, y - k - 1), (x + k + 1, y), (x, y + k + 1), (x - k - 1, y)])
        pygame.draw.polygon(surf, color, [(x, y - k), (x + k, y), (x, y + k), (x - k, y)])
    elif shape == "triangle":
        k = r
        pygame.draw.polygon(surf, OUTLINE, [(x, y - k - 2), (x + k + 2, y + k + 1), (x - k - 2, y + k + 1)])
        pygame.draw.polygon(surf, color, [(x, y - k), (x + k, y + k), (x - k, y + k)])
    else:  # plus
        w = 3 if r >= 5 else 2
        arm = r + 1
        for rect in ((x - arm, y - w // 2, 2 * arm + 1, w), (x - w // 2, y - arm, w, 2 * arm + 1)):
            pygame.draw.rect(surf, OUTLINE, pygame.Rect(rect).inflate(2, 2))
        for rect in ((x - arm, y - w // 2, 2 * arm + 1, w), (x - w // 2, y - arm, w, 2 * arm + 1)):
            pygame.draw.rect(surf, color, rect)


class DotLayer:
    """Screen positions of devices for drawing and tap hit-testing."""

    def __init__(self):
        self.devices: list = []
        self._wx = np.zeros(0)
        self._wy = np.zeros(0)
        self._screen: np.ndarray = np.zeros((0, 2))
        self._visible_idx: np.ndarray = np.zeros(0, dtype=int)

    def set_devices(self, devices: list) -> None:
        located = [d for d in devices if d.lat or d.lon]
        self.devices = located
        if not located:
            self._wx = self._wy = np.zeros(0)
            return
        lon = np.array([d.lon for d in located])
        lat = np.clip(np.array([d.lat for d in located]), -85.05, 85.05)
        s = np.sin(np.radians(lat))
        self._wx = (lon + 180.0) / 360.0
        self._wy = 0.5 - np.log((1 + s) / (1 - s)) / (4 * math.pi)

    def draw(self, surf: pygame.Surface, vp: Viewport, offset: tuple[int, int] = (0, 0)) -> int:
        if not len(self._wx):
            self._visible_idx = np.zeros(0, dtype=int)
            return 0
        sx, sy = vp.to_screen(self._wx, self._wy)
        sx, sy = sx + offset[0], sy + offset[1]
        vis = np.nonzero((sx >= -4) & (sx <= vp.w + 4) & (sy >= -4) & (sy <= vp.h + 4))[0]
        self._screen = np.column_stack([sx, sy])
        self._visible_idx = vis
        radius = 4 if vp.zoom < 16 else 5
        # Draw open networks last so they sit on top.
        order = sorted(vis.tolist(), key=lambda i: self.devices[i].crypt == "OPEN")
        for i in order:
            draw_marker(surf, self.devices[i].crypt, (int(sx[i]), int(sy[i])), radius)
        return len(vis)

    def nearest(self, pos: tuple[int, int], max_px: float = 14):
        if not len(self._visible_idx):
            return None
        pts = self._screen[self._visible_idx]
        d = np.hypot(pts[:, 0] - pos[0], pts[:, 1] - pos[1])
        j = int(np.argmin(d))
        return self.devices[int(self._visible_idx[j])] if d[j] <= max_px else None
