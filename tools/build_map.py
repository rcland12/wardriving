#!/usr/bin/env python3
"""Build an offline vector map for the wardriver's MAP screen from an OpenStreetMap extract.

    pip install osmium shapely numpy
    tools/build_map.py georgia-latest.osm.pbf -o georgia.map --name Georgia

Extracts come from https://download.geofabrik.de (e.g. north-america/us/georgia).
The output is the SQLite tile format described in wardrive/mapdata.py: roads,
waterways, water, parks, buildings, place names, address numbers and named
businesses, at four levels of detail. Copy it to the Pi with scripts/push-map.sh.

Map data © OpenStreetMap contributors, available under the Open Database License.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import osmium
from osmium.filter import KeyFilter
import shapely
from shapely.geometry import LineString, Polygon

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from wardrive.mapdata import EXTENT, FORMAT_VERSION, LOD_TILE_ZOOM, PLACE_KINDS, POI_KINDS, encode_tile  # noqa: E402

ROAD_CLASS = {
    "motorway": 0, "motorway_link": 0,
    "trunk": 1, "trunk_link": 1,
    "primary": 2, "primary_link": 2,
    "secondary": 3, "secondary_link": 3,
    "tertiary": 4, "tertiary_link": 4,
    "residential": 5, "unclassified": 5, "living_street": 5, "road": 5,
    "service": 6,
    "track": 7, "pedestrian": 7, "footway": 7, "path": 7, "cycleway": 7, "steps": 7, "bridleway": 7,
}
# lowest LOD a road class appears in (it's then in every higher LOD too)
ROAD_MIN_LOD = {0: 0, 1: 0, 2: 1, 3: 1, 4: 2, 5: 2, 6: 3, 7: 3}
WATERWAY_CLASS = {"river": 0, "stream": 1, "canal": 1, "drain": 2, "ditch": 2}
WATERWAY_MIN_LOD = {0: 1, 1: 2, 2: 3}
PLACE_MIN_LOD = {"city": 0, "town": 1, "village": 2, "suburb": 2, "hamlet": 3, "neighbourhood": 3}
PARK_TAGS = {("leisure", "park"), ("leisure", "nature_reserve"), ("leisure", "golf_course"), ("leisure", "pitch"),
             ("leisure", "playground"), ("landuse", "recreation_ground"), ("landuse", "cemetery"),
             ("boundary", "national_park")}
WATER_TAGS = {("natural", "water"), ("waterway", "riverbank"), ("landuse", "reservoir"), ("landuse", "basin")}

LARGE_AREA_M2 = 1_000_000  # water/parks this big also appear in LOD 1 (region view)
SMALL_AREA_M2 = 20_000  # smaller ones only in LOD 3 (street view)
SIMPLIFY = {0: 1.0, 1: 1.0, 2: 1.0, 3: 0.5}  # tolerance in tile units (~0.5 px at the LOD's closest zoom)
CLIP_MARGIN = 16  # tile units of overlap kept past each tile edge, so clipped pieces meet without gaps
BATCH = 50_000


def project(coords: list[tuple[float, float]]) -> np.ndarray:
    """[(lon, lat)] -> normalized Web Mercator array (n, 2)."""
    a = np.asarray(coords, dtype=np.float64)
    lat = np.clip(a[:, 1], -85.05112878, 85.05112878)
    s = np.sin(np.radians(lat))
    return np.column_stack([(a[:, 0] + 180.0) / 360.0, 0.5 - np.log((1 + s) / (1 - s)) / (4 * math.pi)])


def area_m2(world: np.ndarray) -> float:
    """Approximate area of a projected ring in square metres."""
    x, y = world[:, 0], world[:, 1]
    a = 0.5 * abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1)))
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * float(y.mean())))))
    return a * (40075016.686 * math.cos(math.radians(lat))) ** 2


def _parts(geom, closed: bool):
    """Coordinate arrays of the polygon rings (closed) or line strings in a clipped geometry."""
    if geom.is_empty:
        return
    kinds = ("Polygon",) if closed else ("LineString",)
    for g in getattr(geom, "geoms", [geom]):
        if g.geom_type in kinds:
            coords = np.asarray(g.exterior.coords if closed else g.coords)
            if len(coords) >= (4 if closed else 2):
                yield coords
        elif g.geom_type.startswith("Multi") or g.geom_type == "GeometryCollection":
            yield from _parts(g, closed)


class Builder:
    def __init__(self, staging: Path):
        staging.unlink(missing_ok=True)
        self.db = sqlite3.connect(staging)
        self.db.execute("pragma journal_mode=off")
        self.db.execute("pragma synchronous=off")
        self.db.execute("create table staging (lod int, tx int, ty int, layer text, rec text)")
        self.pending: list[tuple] = []
        self.counts: dict[str, int] = {}
        self.bounds = [180.0, 90.0, -180.0, -90.0]

    # --- output ------------------------------------------------------------------

    def _emit(self, lod: int, layer: str, world: np.ndarray, make_rec, closed: bool = False) -> None:
        """Place a feature (projected coords) into every tile of `lod` it touches."""
        z = LOD_TILE_ZOOM[lod]
        units = world * (2**z * EXTENT)
        tol = SIMPLIFY[lod]
        if tol and len(units) > 2:
            geom = Polygon(units) if closed and len(units) >= 4 else LineString(units)
            geom = geom.simplify(tol, preserve_topology=False)
            if geom.is_empty:
                return
            if geom.geom_type == "Polygon":
                units = np.asarray(geom.exterior.coords)
            elif geom.geom_type == "LineString":
                units = np.asarray(geom.coords)
            else:
                return
        pts = np.round(units).astype(np.int64)
        keep = np.ones(len(pts), dtype=bool)
        keep[1:] = np.any(pts[1:] != pts[:-1], axis=1)
        pts = pts[keep]
        if len(pts) < (3 if closed else 2):
            return
        tx0, ty0 = (pts.min(axis=0) // EXTENT).tolist()
        tx1, ty1 = (pts.max(axis=0) // EXTENT).tolist()
        whole = tx0 == tx1 and ty0 == ty1
        geom = None if whole else (Polygon(pts) if closed else LineString(pts))
        for ty in range(ty0, ty1 + 1):
            for tx in range(tx0, tx1 + 1):
                if whole:
                    pieces = [pts]
                else:
                    # Spans several tiles: store only this tile's part (plus a small overlap).
                    x0, y0 = tx * EXTENT - CLIP_MARGIN, ty * EXTENT - CLIP_MARGIN
                    x1, y1 = x0 + EXTENT + 2 * CLIP_MARGIN, y0 + EXTENT + 2 * CLIP_MARGIN
                    try:
                        clipped = shapely.clip_by_rect(geom, x0, y0, x1, y1)
                    except shapely.errors.GEOSException:
                        geom = shapely.make_valid(geom)  # e.g. a self-intersecting OSM polygon
                        clipped = shapely.clip_by_rect(geom, x0, y0, x1, y1)
                    pieces = list(_parts(clipped, closed))
                for piece in pieces:
                    local = (np.round(piece).astype(np.int64) - (tx * EXTENT, ty * EXTENT)).ravel().tolist()
                    self.pending.append((lod, tx, ty, layer, json.dumps(make_rec(local), separators=(",", ":"))))
        self.counts[layer] = self.counts.get(layer, 0) + 1
        if len(self.pending) >= BATCH:
            self.flush()

    def _emit_point(self, lod: int, layer: str, lon: float, lat: float, make_rec) -> None:
        z = LOD_TILE_ZOOM[lod]
        wx, wy = project([(lon, lat)])[0] * (2**z * EXTENT)
        x, y = int(round(wx)), int(round(wy))
        tx, ty = x // EXTENT, y // EXTENT
        self.pending.append((lod, tx, ty, layer, json.dumps(make_rec(x - tx * EXTENT, y - ty * EXTENT), separators=(",", ":"))))
        self.counts[layer] = self.counts.get(layer, 0) + 1

    def flush(self) -> None:
        self.db.executemany("insert into staging values (?,?,?,?,?)", self.pending)
        self.pending.clear()

    def _grow_bounds(self, coords) -> None:
        for lon, lat in coords:
            b = self.bounds
            b[0], b[1], b[2], b[3] = min(b[0], lon), min(b[1], lat), max(b[2], lon), max(b[3], lat)

    # --- features ----------------------------------------------------------------

    def road(self, way) -> None:
        tags = way.tags
        cls = ROAD_CLASS.get(tags.get("highway", ""))
        if cls is None or tags.get("area") == "yes":
            return
        try:
            coords = [(n.lon, n.lat) for n in way.nodes]
        except osmium.InvalidLocationError:
            return
        if len(coords) < 2:
            return
        self._grow_bounds(coords[:: max(1, len(coords) // 2)])
        world = project(coords)
        name = tags.get("name") or (tags.get("ref", "").replace(";", " / ") if cls <= 1 else "")
        for lod in range(ROAD_MIN_LOD[cls], 4):
            self._emit(lod, "r", world, lambda c, w=way.id: [w, cls, name, c])

    def waterway(self, way) -> None:
        cls = WATERWAY_CLASS.get(way.tags.get("waterway", ""))
        if cls is None:
            return
        try:
            coords = [(n.lon, n.lat) for n in way.nodes]
        except osmium.InvalidLocationError:
            return
        if len(coords) < 2:
            return
        world = project(coords)
        name = way.tags.get("name", "")
        for lod in range(WATERWAY_MIN_LOD[cls], 4):
            self._emit(lod, "l", world, lambda c, w=way.id: [w, cls, name, c])

    def area(self, area) -> None:
        tags = area.tags
        building = tags.get("building", "no") != "no"
        water = any((k, tags.get(k)) in WATER_TAGS for k in ("natural", "waterway", "landuse"))
        park = any((k, tags.get(k)) in PARK_TAGS for k in ("leisure", "landuse", "boundary"))
        if not (building or water or park):
            return
        try:
            rings = [[(n.lon, n.lat) for n in ring] for ring in area.outer_rings()]
        except osmium.InvalidLocationError:
            return
        for ring in rings:
            if len(ring) < 4:
                continue
            world = project(ring)
            if building:
                self._emit(3, "b", world, lambda c, i=area.id: [i, c], closed=True)
                cx, cy = world.mean(axis=0)
                lon = cx * 360.0 - 180.0
                lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * cy))))
                if tags.get("addr:housenumber"):
                    self._emit_point(3, "a", lon, lat, lambda x, y, n=tags["addr:housenumber"]: [n, x, y])
                self._poi(tags, lon, lat)
                continue
            size = area_m2(world)
            layer = "w" if water else "p"
            lods = ([1] if size >= LARGE_AREA_M2 else []) + ([2] if size >= SMALL_AREA_M2 else [3])
            for lod in lods:
                self._emit(lod, layer, world, lambda c, i=area.id: [i, c], closed=True)

    def _poi(self, tags, lon: float, lat: float) -> None:
        name = tags.get("name")
        if not name:
            return
        for kind, key in enumerate(POI_KINDS):
            if key in tags:
                self._emit_point(3, "i", lon, lat, lambda x, y, k=kind, n=name: [k, n, x, y])
                return

    def node(self, node) -> None:
        tags = node.tags
        loc = node.location
        if not loc.valid():
            return
        place = tags.get("place")
        if place in PLACE_MIN_LOD and tags.get("name"):
            kind = PLACE_KINDS.index(place)
            rank = place_rank(tags)
            for lod in range(PLACE_MIN_LOD[place], 4):
                # rank is only used to order the tile's places and is dropped in write()
                self._emit_point(lod, "t", loc.lon, loc.lat, lambda x, y, k=kind, n=tags["name"], r=rank: [k, n, x, y, r])
            return
        if tags.get("addr:housenumber"):
            self._emit_point(3, "a", loc.lon, loc.lat, lambda x, y, n=tags["addr:housenumber"]: [n, x, y])
        self._poi(tags, loc.lon, loc.lat)

    # --- assembly ----------------------------------------------------------------

    def write(self, out: Path, name: str, source: str) -> dict:
        self.flush()
        print("indexing staged features…", flush=True)
        self.db.execute("create index staging_tile on staging (lod, tx, ty)")
        self.db.commit()
        out.unlink(missing_ok=True)
        dst = sqlite3.connect(out)
        dst.execute("create table meta (key text primary key, value text)")
        dst.execute("create table tiles (lod integer, x integer, y integer, data blob, primary key (lod, x, y))")
        stats = {"tiles": 0, "bytes": 0}
        current, tile, names, batch = None, None, None, []

        def finish():
            if tile is None:
                return
            if "t" in tile:  # biggest places first, so they win label collisions
                tile["t"].sort(key=lambda r: (r[0], -r[4]))
                tile["t"] = [r[:4] for r in tile["t"]]
            tile["n"] = names
            blob = encode_tile(tile)
            batch.append((*current, blob))
            stats["tiles"] += 1
            stats["bytes"] += len(blob)

        def name_index(value: str) -> int:
            if not value:
                return -1
            idx = name_ids.get(value)
            if idx is None:
                idx = name_ids[value] = len(names)
                names.append(value)
            return idx

        name_ids: dict[str, int] = {}
        for lod, tx, ty, layer, rec in self.db.execute("select lod, tx, ty, layer, rec from staging order by lod, tx, ty"):
            key = (lod, tx, ty)
            if key != current:
                finish()
                if len(batch) >= 2000:
                    dst.executemany("insert into tiles values (?,?,?,?)", batch)
                    batch.clear()
                current, tile, names, name_ids = key, {}, [], {}
            r = json.loads(rec)
            if layer in ("r", "l"):
                r[2] = name_index(r[2])
            elif layer in ("t", "i"):
                r[1] = name_index(r[1])
            elif layer == "a":
                r[0] = name_index(r[0])
            tile.setdefault(layer, []).append(r)
        finish()
        dst.executemany("insert into tiles values (?,?,?,?)", batch)
        w, s, e, n = self.bounds
        meta = {
            "format": str(FORMAT_VERSION),
            "name": name,
            "bounds": f"{w:.5f},{s:.5f},{e:.5f},{n:.5f}",
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": source,
            "attribution": "© OpenStreetMap contributors",
            "lods": json.dumps(LOD_TILE_ZOOM),
            "features": json.dumps(self.counts),
        }
        dst.executemany("insert into meta values (?,?)", meta.items())
        dst.commit()
        dst.execute("vacuum")
        dst.close()
        return stats


def place_rank(tags) -> int:
    """Label priority among places of the same kind: capitals, then population."""
    digits = "".join(ch for ch in tags.get("population", "").split(";")[0].split(".")[0] if ch.isdigit())
    population = int(digits) if digits else 0
    capital = tags.get("capital", "") in ("yes", "2", "4")
    return population + (100_000_000 if capital else 0)


def build(src: Path, out: Path, name: str, staging: Path | None = None) -> dict:
    staging = staging or out.with_suffix(".staging.sqlite")
    builder = Builder(staging)
    keys = ("highway", "waterway", "building", "natural", "leisure", "landuse", "boundary", "place",
            "addr:housenumber", "amenity", "shop", "tourism", "office")
    processor = (
        osmium.FileProcessor(str(src))
        .with_locations("flex_mem")
        .with_areas(KeyFilter("building", "natural", "waterway", "landuse", "leisure", "boundary"))
        .with_filter(KeyFilter(*keys))
    )
    started, seen = time.monotonic(), 0
    for obj in processor:
        seen += 1
        if obj.is_way():
            if "highway" in obj.tags:
                builder.road(obj)
            elif "waterway" in obj.tags:
                builder.waterway(obj)
        elif obj.is_area():
            builder.area(obj)
        elif obj.is_node():
            builder.node(obj)
        if seen % 500_000 == 0:
            print(f"  {seen:,} objects, {time.monotonic() - started:.0f}s, {builder.counts}", flush=True)
    stats = builder.write(out, name, src.name)
    builder.db.close()
    staging.unlink(missing_ok=True)
    stats["features"] = builder.counts
    stats["seconds"] = round(time.monotonic() - started)
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path, help="OpenStreetMap extract (.osm.pbf or .osm)")
    parser.add_argument("-o", "--output", type=Path, required=True, help="output .map file")
    parser.add_argument("--name", default=None, help="display name (default: output file stem)")
    args = parser.parse_args()
    stats = build(args.source, args.output, args.name or args.output.stem.title())
    size = args.output.stat().st_size
    print(f"wrote {args.output} ({size / 1e6:.1f} MB, {stats['tiles']:,} tiles) in {stats['seconds']}s")
    print("features:", stats["features"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
