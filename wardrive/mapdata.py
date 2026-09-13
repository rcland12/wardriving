"""Offline vector map: projection math, tile format, and the reader used on the Pi.

A map file is SQLite with two tables:

    meta(key TEXT PRIMARY KEY, value TEXT)
    tiles(lod INTEGER, x INTEGER, y INTEGER, data BLOB, PRIMARY KEY (lod, x, y))

Features are stored at four levels of detail (LODs). Each LOD is cut into Web
Mercator tiles at its own tile zoom, and each tile's `data` is zlib-compressed
JSON. Coordinates are integers in the tile's local grid of EXTENT units per side
(they may fall slightly outside 0..EXTENT: features are clipped to each tile they
touch with a small overlap, so neighbouring tiles join without gaps).

Tile JSON (every key optional):

    "n": [names...]                         shared string table
    "r": [[id, class, name, [x,y,...]]]     roads (class: see ROAD_CLASSES)
    "l": [[id, class, name, [x,y,...]]]     waterway lines (0 river, 1 stream/canal)
    "w": [[id, [x,y,...]]]                  water polygons (outer ring)
    "p": [[id, [x,y,...]]]                  parks and green areas (outer ring)
    "b": [[id, [x,y,...]]]                  buildings (outer ring)
    "t": [[kind, name, x, y]]               places (kind: see PLACE_KINDS)
    "a": [[name, x, y]]                     address numbers
    "i": [[kind, name, x, y]]               named businesses / points of interest

`name` fields are indexes into "n", or -1.
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
import zlib
from collections import OrderedDict
from pathlib import Path

EXTENT = 4096
FORMAT_VERSION = 1
TILE_SIZE = 256  # display pixels per tile at its own zoom

# LOD -> Mercator zoom its tiles are cut at
LOD_TILE_ZOOM = {0: 5, 1: 8, 2: 11, 3: 14}

ROAD_CLASSES = ["motorway", "trunk", "primary", "secondary", "tertiary", "residential", "service", "minor"]
PLACE_KINDS = ["city", "town", "village", "suburb", "hamlet", "neighbourhood"]
POI_KINDS = ["amenity", "shop", "tourism", "office", "leisure"]

MIN_ZOOM, MAX_ZOOM = 6, 19


def road_lod(zoom: int) -> int:
    """Which LOD holds the right amount of road detail for a display zoom."""
    return 0 if zoom <= 8 else 1 if zoom <= 11 else 2 if zoom <= 14 else 3


def area_lod(zoom: int) -> int:
    return 1 if zoom <= 11 else 2


def place_kinds_for(zoom: int) -> int:
    """Number of PLACE_KINDS (from the start) worth labelling at this zoom."""
    return 1 if zoom <= 8 else 2 if zoom <= 11 else 4 if zoom <= 14 else len(PLACE_KINDS)


# --- Web Mercator ----------------------------------------------------------------------


def lonlat_to_world(lon: float, lat: float) -> tuple[float, float]:
    """(lon, lat) -> normalized Web Mercator (0..1, y down)."""
    lat = max(min(lat, 85.05112878), -85.05112878)
    x = (lon + 180.0) / 360.0
    s = math.sin(math.radians(lat))
    y = 0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)
    return x, y


def world_to_lonlat(x: float, y: float) -> tuple[float, float]:
    lon = x * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y))))
    return lon, lat


def meters_per_pixel(lat: float, zoom: float) -> float:
    return 40075016.686 * math.cos(math.radians(lat)) / (TILE_SIZE * 2**zoom)


# --- tile encoding ---------------------------------------------------------------------


def encode_tile(tile: dict) -> bytes:
    return zlib.compress(json.dumps(tile, separators=(",", ":")).encode(), 6)


def decode_tile(blob: bytes) -> dict:
    return json.loads(zlib.decompress(blob))


# --- reader ----------------------------------------------------------------------------


class MapFile:
    """Read-only access to a .map file with a small decoded-tile cache."""

    def __init__(self, path: str | Path, cache_tiles: int = 64):
        self.path = Path(path)
        self._con = sqlite3.connect(f"file:{self.path}?mode=ro&immutable=1", uri=True, check_same_thread=False)
        self.meta = dict(self._con.execute("select key, value from meta"))
        if int(self.meta.get("format", 0)) != FORMAT_VERSION:
            raise ValueError(f"{self.path.name}: unsupported map format {self.meta.get('format')}")
        self.name = self.meta.get("name", self.path.stem)
        self.attribution = self.meta.get("attribution", "© OpenStreetMap contributors")
        self.bounds = tuple(float(v) for v in self.meta.get("bounds", "-180,-85,180,85").split(","))  # w,s,e,n
        self._cache: OrderedDict[tuple[int, int, int], dict] = OrderedDict()
        self._cache_size = cache_tiles

    def tile(self, lod: int, x: int, y: int) -> dict:
        key = (lod, x, y)
        tile = self._cache.get(key)
        if tile is not None:
            self._cache.move_to_end(key)
            return tile
        row = self._con.execute("select data from tiles where lod=? and x=? and y=?", key).fetchone()
        tile = decode_tile(row[0]) if row else {}
        self._cache[key] = tile
        if len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return tile

    def tiles_in(self, lod: int, x0: float, y0: float, x1: float, y1: float):
        """Yield (tx, ty, tile) for tiles of `lod` covering the world rectangle (0..1 units)."""
        z = LOD_TILE_ZOOM[lod]
        n = 2**z
        for ty in range(max(0, math.floor(y0 * n)), min(n - 1, math.floor(y1 * n)) + 1):
            for tx in range(max(0, math.floor(x0 * n)), min(n - 1, math.floor(x1 * n)) + 1):
                tile = self.tile(lod, tx, ty)
                if tile:
                    yield tx, ty, tile

    def center(self) -> tuple[float, float]:
        w, s, e, n = self.bounds
        return (w + e) / 2, (s + n) / 2

    def close(self) -> None:
        self._con.close()


def find_map(directory: str | Path) -> MapFile | None:
    """The first usable *.map file in a directory (alphabetically), or None."""
    try:
        files = sorted(Path(directory).glob("*.map"))
    except OSError:
        return None
    for path in files:
        try:
            return MapFile(path)
        except Exception as exc:  # corrupt or unsupported file: try the next one
            logging.getLogger(__name__).warning("cannot open map %s: %s", path, exc)
    return None
