"""Offline map: projection, tile format, builder (if pyosmium is installed), renderer, MAP view."""

import math
import os
import time
from pathlib import Path

import pytest

os.environ["SDL_VIDEODRIVER"] = "dummy"

from wardrive import config  # noqa: E402
from wardrive.mapdata import (  # noqa: E402
    EXTENT, LOD_TILE_ZOOM, MapFile, decode_tile, encode_tile, lonlat_to_world, road_lod, world_to_lonlat,
)
from wardrive.state import State  # noqa: E402
from wardrive.touch import TouchEvent  # noqa: E402

OSM = """<?xml version='1.0' encoding='UTF-8'?>
<osm version="0.6">
 <node id="1" lat="33.7490" lon="-84.3880"/>
 <node id="2" lat="33.7500" lon="-84.3880"/>
 <node id="3" lat="33.7500" lon="-84.3870"/>
 <node id="4" lat="33.7490" lon="-84.3870"/>
 <node id="5" lat="33.7495" lon="-84.3890"><tag k="place" v="city"/><tag k="name" v="Testlanta"/></node>
 <node id="13" lat="33.7493" lon="-84.3885"><tag k="place" v="city"/><tag k="name" v="Smalltown"/><tag k="population" v="900"/></node>
 <node id="14" lat="33.7497" lon="-84.3886"><tag k="place" v="city"/><tag k="name" v="Bigville"/><tag k="population" v="1,200,000"/></node>
 <node id="6" lat="33.7495" lon="-84.3875"><tag k="amenity" v="cafe"/><tag k="name" v="Bean There"/></node>
 <node id="7" lat="33.7480" lon="-84.3900"/>
 <node id="8" lat="33.7480" lon="-84.3800"/>
 <node id="9" lat="33.7470" lon="-84.3900"/>
 <node id="10" lat="33.7470" lon="-84.3890"/>
 <node id="11" lat="33.7460" lon="-84.3890"/>
 <node id="12" lat="33.7460" lon="-84.3900"/>
 <way id="20"><nd ref="7"/><nd ref="8"/><tag k="highway" v="residential"/><tag k="name" v="Peachtree Test St"/></way>
 <way id="21"><nd ref="7"/><nd ref="8"/><tag k="highway" v="motorway"/><tag k="ref" v="I 75"/></way>
 <way id="22"><nd ref="1"/><nd ref="2"/><nd ref="3"/><nd ref="4"/><nd ref="1"/><tag k="building" v="yes"/><tag k="addr:housenumber" v="42"/></way>
 <way id="23"><nd ref="9"/><nd ref="10"/><nd ref="11"/><nd ref="12"/><nd ref="9"/><tag k="natural" v="water"/></way>
 <way id="24"><nd ref="7"/><nd ref="9"/><tag k="waterway" v="stream"/></way>
 <node id="30" lat="33.7400" lon="-84.4400"/>
 <node id="31" lat="33.7400" lon="-84.3300"/>
 <way id="25"><nd ref="30"/><nd ref="31"/><tag k="highway" v="primary"/><tag k="name" v="Long Test Rd"/></way>
</osm>
"""


def test_projection_round_trip_and_known_points():
    assert lonlat_to_world(0, 0) == pytest.approx((0.5, 0.5))
    for lon, lat in [(-84.388, 33.749), (-81.09, 32.08), (179.9, -60)]:
        x, y = lonlat_to_world(lon, lat)
        assert world_to_lonlat(x, y) == pytest.approx((lon, lat), abs=1e-9)
    assert road_lod(8) == 0 and road_lod(12) == 2 and road_lod(19) == 3


def test_tile_encoding_round_trip():
    tile = {"r": [[1, 5, 0, [0, 0, 10, 10]]], "n": ["Main St"]}
    assert decode_tile(encode_tile(tile)) == tile


@pytest.fixture(scope="module")
def built_map(tmp_path_factory):
    pytest.importorskip("osmium")
    pytest.importorskip("shapely")
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    import build_map

    d = tmp_path_factory.mktemp("map")
    (d / "t.osm").write_text(OSM)
    build_map.build(d / "t.osm", d / "test.map", "Test")
    return d / "test.map"


def test_builder_writes_every_layer(built_map):
    m = MapFile(built_map)
    assert m.name == "Test" and "OpenStreetMap" in m.attribution
    x, y = lonlat_to_world(-84.3875, 33.7490)
    layers, names = set(), set()
    for lod in LOD_TILE_ZOOM:
        for _, _, tile in m.tiles_in(lod, x - 0.001, y - 0.001, x + 0.001, y + 0.001):
            layers |= {k for k in tile if k != "n"}
            names |= set(tile.get("n", []))
    assert {"r", "b", "w", "t", "a", "i", "l"} <= layers
    assert {"Peachtree Test St", "I 75", "Testlanta", "42", "Bean There"} <= names

    # The motorway is in every LOD; the residential street only from LOD 2.
    def roads(lod):
        found = set()
        for _, _, tile in m.tiles_in(lod, x - 0.001, y - 0.001, x + 0.001, y + 0.001):
            found |= {tile["n"][r[2]] for r in tile.get("r", []) if r[2] >= 0}
        return found

    assert "I 75" in roads(0) and "Peachtree Test St" not in roads(0) | roads(1)
    assert {"I 75", "Peachtree Test St"} <= roads(2)


def test_builder_orders_places_by_importance(built_map):
    m = MapFile(built_map)
    x, y = lonlat_to_world(-84.3885, 33.7495)
    for lod in LOD_TILE_ZOOM:
        for _, _, tile in m.tiles_in(lod, x - 1e-6, y - 1e-6, x + 1e-6, y + 1e-6):
            order = [tile["n"][p[1]] for p in tile["t"]]
            assert order.index("Bigville") < order.index("Smalltown")
            assert all(len(p) == 4 for p in tile["t"])


def test_builder_coordinates_land_where_expected(built_map):
    m = MapFile(built_map)
    lod, z = 3, LOD_TILE_ZOOM[3]
    x, y = lonlat_to_world(-84.3875, 33.7495)  # centre of the building
    tx, ty = int(x * 2**z), int(y * 2**z)
    tile = m.tile(lod, tx, ty)
    ring = tile["b"][0][1]
    xs, ys = ring[0::2], ring[1::2]
    cx = (tx * EXTENT + sum(xs) / len(xs)) / (2**z * EXTENT)
    cy = (ty * EXTENT + sum(ys) / len(ys)) / (2**z * EXTENT)
    lon, lat = world_to_lonlat(cx, cy)
    assert abs(lon - -84.3875) < 0.0003 and abs(lat - 33.7495) < 0.0003


def test_builder_clips_long_features_to_each_tile(built_map):
    m = MapFile(built_map)
    z = LOD_TILE_ZOOM[3]
    y = lonlat_to_world(-84.40, 33.7400)[1]
    x0, x1 = lonlat_to_world(-84.44, 33.74)[0], lonlat_to_world(-84.33, 33.74)[0]
    pieces = []
    for tx, ty, tile in m.tiles_in(3, x0, y - 1e-6, x1, y + 1e-6):
        for rec in tile.get("r", []):
            if tile["n"][rec[2]] == "Long Test Rd":
                xs = rec[3][0::2]
                assert min(xs) >= -16 and max(xs) <= EXTENT + 16  # only this tile's part (plus overlap)
                pieces.append(tx)
    assert len(set(pieces)) >= 2  # an 10 km road spans several street-level tiles


def test_renderer_draws_real_features(built_map):
    import pygame

    from wardrive.maprender import MapRenderer, Viewport

    pygame.display.init()
    pygame.font.init()
    m = MapFile(built_map)
    surf = pygame.Surface((368, 268))
    x, y = lonlat_to_world(-84.3875, 33.7481)
    for zoom in (6, 10, 13, 16, 18, 19):
        stats = MapRenderer(m).render(surf, Viewport(x, y, zoom, 368, 268))
        assert stats.ms < 2000
    # Zoom 16 around the building, water and roads: every layer draws.
    x16, y16 = lonlat_to_world(-84.3880, 33.7480)
    stats = MapRenderer(m).render(surf, Viewport(x16, y16, 16, 368, 268))
    assert stats.lines >= 3 and stats.polygons >= 2
    # Zoom 18: the street's only segment runs far off-screen, but its visible part still gets a label.
    stats = MapRenderer(m).render(surf, Viewport(x, y, 18, 368, 268))
    assert stats.labels >= 1
    assert MapRenderer(None).render(surf, Viewport(x, y, 12, 368, 268)).tiles == 0  # no map: grid only


def test_clip_polygon_to_view():
    import numpy as np

    from wardrive.maprender import _clip_polygon

    square = np.array([[-1000, -1000], [1000, -1000], [1000, 1000], [-1000, 1000]], dtype=np.float32)
    clipped = _clip_polygon(square, 0, 0, 100, 50)
    assert clipped[:, 0].min() == 0 and clipped[:, 0].max() == 100 and clipped[:, 1].max() == 50
    outside = np.array([[200, 200], [300, 200], [300, 300]], dtype=np.float32)
    assert len(_clip_polygon(outside, 0, 0, 100, 100)) == 0


def test_dot_layer_hit_testing():
    import pygame

    from wardrive.maprender import DotLayer, Viewport
    from wardrive.state import Device

    pygame.display.init()
    dev = Device("k", "AA", "wifi", "cafe", "6", "OPEN", "Open", "", -50, 0, 0, lat=33.749, lon=-84.388)
    nofix = Device("k2", "BB", "wifi", "x", "6", "WPA2", "", "", -50, 0, 0)
    layer = DotLayer()
    layer.set_devices([dev, nofix])
    assert layer.devices == [dev]
    vp = Viewport(*lonlat_to_world(-84.388, 33.749), 16, 368, 268)
    assert layer.draw(pygame.Surface((368, 268)), vp) == 1
    assert layer.nearest((184, 134)) is dev
    assert layer.nearest((10, 10)) is None


# --- MAP view ------------------------------------------------------------------------


@pytest.fixture
def app(tmp_path, built_map):
    import shutil

    from wardrive.app import App
    from wardrive.display import Display
    from wardrive.mock import MockBackend

    maps = tmp_path / "maps"
    maps.mkdir()
    shutil.copy(built_map, maps / "test.map")
    cfg = config.Config()
    cfg.touch.calibration_file = str(tmp_path / "touch.json")
    cfg.map.dir = str(maps)
    state = State()
    backend = MockBackend(cfg, state)
    return App(cfg, state, backend, Display("headless"), touch=None)


def tap(app, pos):
    app._dispatch(TouchEvent("down", pos, pos))
    app._dispatch(TouchEvent("up", pos, pos))


def test_map_nav_zoom_pan_and_tap(app):
    from wardrive.views.map import AREA

    tap(app, app.nav[4].rect.center)
    view = app.current
    assert view.name == "map" and view.map is not None
    app.tick()

    zoom = view.vp.zoom
    tap(app, view.buttons[1].rect.center)  # +
    assert view.vp.zoom == zoom + 1
    tap(app, view.buttons[2].rect.center)  # −
    assert view.vp.zoom == zoom

    cx = view.vp.cx
    start, end = (AREA.centerx, AREA.centery), (AREA.centerx - 100, AREA.centery)
    app._dispatch(TouchEvent("down", start, start))
    app._dispatch(TouchEvent("move", end, end))
    app.tick()  # drawn from the shifted cached image while dragging
    app._dispatch(TouchEvent("up", end, end))
    assert view.vp.cx == pytest.approx(cx + 100 / view.vp.scale) and not view.follow
    app.tick()

    # Put a live device under the map centre and tap it.
    from wardrive.state import Device

    lon, lat = world_to_lonlat(view.vp.cx, view.vp.cy)
    with app.state.lock:
        app.state.devices["x"] = Device("x", "AA:BB", "wifi", "Tap Me", "6", "OPEN", "Open", "", -40, time.time(), time.time(), lat=lat, lon=lon)
        app.state.version += 1
    view._live_at = 0
    app.tick()
    tap(app, AREA.center)
    assert app.modal is not None and app.modal.device.name == "Tap Me"


def test_map_saved_mode_and_open_from_session(app):
    view = app.views["map"]
    app.show("map")
    tap(app, view.buttons[0].rect.center)  # LIVE -> SAVED (all sessions)
    assert view.mode == "saved"
    view.loader.wait()
    app.tick()
    assert len(view.dots.devices) > 0

    nets = app.views["session_nets"]
    nets.open("wardrive-mock-2", "Sat test")
    app.show("session_nets")
    tap(app, nets.buttons[3].rect.center)  # MAP
    assert app.current is view and view.session == "wardrive-mock-2" and view.mode == "saved"
    view.loader.wait()
    app.tick()
    assert len(view.dots.devices) == 90
    lon, lat = world_to_lonlat(view.vp.cx, view.vp.cy)
    assert abs(lat - sum(d.lat for d in view.dots.devices) / 90) < 1e-6  # centred on the session


def test_live_map_zooms_to_first_fix_then_follows(app):
    view = app.views["map"]
    app.show("map")
    assert view.vp.zoom == 7  # region view with no fix
    g = app.state.gps
    g.gpsd_up, g.mode, g.lat, g.lon, g.last_report = True, 3, 33.7485, -84.3880, time.monotonic() + 60
    app.tick()
    assert view.vp.zoom == app.cfg.map.default_zoom and view.follow
    assert world_to_lonlat(view.vp.cx, view.vp.cy) == pytest.approx((-84.3880, 33.7485))
    g.lat = 33.7490
    app.tick()
    assert world_to_lonlat(view.vp.cx, view.vp.cy)[1] == pytest.approx(33.7490)  # following


def test_map_without_map_file(tmp_path):
    from wardrive.app import App
    from wardrive.display import Display
    from wardrive.mock import MockBackend

    cfg = config.Config()
    cfg.touch.calibration_file = str(tmp_path / "touch.json")
    cfg.map.dir = str(tmp_path / "nothing-here")
    state = State()
    app = App(cfg, state, MockBackend(cfg, state), Display("headless"), touch=None)
    app.show("map")
    assert app.current.map is None
    app.tick()
