"""Demo mode: simulated drives, Kismet-format output, the demo backend, and switching modes."""

import gzip
import json
import math
import os
import random
import shutil
import sqlite3
import sys
import time
from pathlib import Path

import pytest

os.environ["SDL_VIDEODRIVER"] = "dummy"

from wardrive import config, demo  # noqa: E402
from wardrive.demo import Drive  # noqa: E402
from wardrive.demo.files import DEMO_MARKER, SessionRecorder  # noqa: E402
from wardrive.demo.world import GridDriver, MapIndex, RoadDriver, Scanner, World, make_driver  # noqa: E402
from wardrive.mapdata import EXTENT, FORMAT_VERSION, LOD_TILE_ZOOM, MapFile, encode_tile, lonlat_to_world, world_to_lonlat  # noqa: E402
from wardrive.sessions import SessionLibrary  # noqa: E402
from wardrive.state import Capture, State  # noqa: E402
from wardrive.uploads import UploadManager  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import wardrive_review as wr  # noqa: E402

HOME = (33.7490, -84.3880)
SMALL_PLAN = [Drive(2, 18.0, 4), Drive(1, 9.0, 5, stale_clock=True)]


@pytest.fixture
def cfg(tmp_path):
    c = config.Config()
    c.demo.dir = str(tmp_path / "demo")
    c.map.dir = str(tmp_path / "maps")  # empty: grid driving
    c.touch.calibration_file = str(tmp_path / "touch.json")
    c.demo.lat, c.demo.lon = HOME
    return c


def grid_map(path: Path, lat: float, lon: float, blocks: int = 6, spacing_m: float = 120) -> Path:
    """A street-level map of a square street grid around (lat, lon), without pyosmium."""
    z = LOD_TILE_ZOOM[3]
    n = 2**z
    half = blocks * spacing_m / 2
    dlat, dlon = half / 111_320, half / (111_320 * math.cos(math.radians(lat)))
    x0, y1 = lonlat_to_world(lon - dlon, lat - dlat)
    x1, y0 = lonlat_to_world(lon + dlon, lat + dlat)
    lines = []
    for i in range(blocks + 1):
        f = i / blocks
        lines.append([(x0 + f * (x1 - x0), y0), (x0 + f * (x1 - x0), y1)])
        lines.append([(x0, y0 + f * (y1 - y0)), (x1, y0 + f * (y1 - y0))])
    tiles: dict[tuple[int, int], dict] = {}
    for rid, line in enumerate(lines):
        # Split every street into short pieces so pieces meet at shared points, like clipped tiles.
        steps = 24
        pts = [(line[0][0] + (line[1][0] - line[0][0]) * k / steps, line[0][1] + (line[1][1] - line[0][1]) * k / steps)
               for k in range(steps + 1)]
        for a, b in zip(pts, pts[1:]):
            tx, ty = int(min(a[0], b[0]) * n), int(min(a[1], b[1]) * n)
            coords = [round(v) for p in (a, b) for v in ((p[0] * n - tx) * EXTENT, (p[1] * n - ty) * EXTENT)]
            tiles.setdefault((tx, ty), {"n": [], "r": []})["r"].append([rid, 5, -1, coords])
    con = sqlite3.connect(path)
    con.execute("create table meta (key text primary key, value text)")
    con.execute("create table tiles (lod integer, x integer, y integer, data blob, primary key (lod, x, y))")
    w, s = world_to_lonlat(x0, y1)
    e, nn = world_to_lonlat(x1, y0)
    con.executemany("insert into meta values (?,?)", [("format", str(FORMAT_VERSION)), ("name", "Grid"),
                                                      ("bounds", f"{w},{s},{e},{nn}")])
    con.executemany("insert into tiles values (3,?,?,?)", [(tx, ty, encode_tile(t)) for (tx, ty), t in tiles.items()])
    con.commit()
    con.close()
    return path


# --- world ------------------------------------------------------------------------


def test_world_is_the_same_every_time_and_differs_by_seed():
    a, b, c = World(None, seed=1), World(None, seed=1), World(None, seed=2)
    macs = lambda w: sorted(d.mac for d in w.around(*HOME, 400))  # noqa: E731
    assert macs(a) == macs(b) and macs(a) != macs(c)
    assert len(macs(a)) > 5


def test_scanner_hears_near_networks_more_than_far_ones():
    world = World(None, seed=3)
    scanner = Scanner(world, random.Random(1))
    heard: dict[str, int] = {}
    for t in range(60):
        for dev, rssi in scanner.scan(t, *HOME, 0.0):
            if dev.phy == "wifi":
                heard[dev.key] = heard.get(dev.key, 0) + 1
    assert heard
    by_key = {d.key: d for d in world.around(*HOME, 400)}

    def metres(k):
        d = by_key[k]
        return math.hypot((d.lat - HOME[0]) * 111_320, (d.lon - HOME[1]) * 111_320 * math.cos(math.radians(HOME[0])))

    near = [k for k in heard if metres(k) < 60]
    far = [k for k in heard if metres(k) > 200]
    if near and far:
        assert max(heard[k] for k in near) >= max(heard[k] for k in far)
    assert all(metres(k) < 400 for k in heard)


def test_road_driver_stays_on_the_roads(tmp_path):
    lat, lon = 32.6130, -83.6242
    mapfile = MapFile(grid_map(tmp_path / "grid.map", lat, lon))
    driver = make_driver(MapIndex(mapfile), lat, lon, random.Random(5))
    assert isinstance(driver, RoadDriver)
    streets_lon = {round(lon + (i - 3) * 120 / (111_320 * math.cos(math.radians(lat))), 5) for i in range(7)}
    streets_lat = {round(lat + (i - 3) * 120 / 111_320, 5) for i in range(7)}
    driven, cells = 0.0, set()
    for _ in range(600):
        driven += driver.step(1.0)
        off_lon = min(abs(driver.lon - s) for s in streets_lon) * 111_320 * math.cos(math.radians(lat))
        off_lat = min(abs(driver.lat - s) for s in streets_lat) * 111_320
        assert min(off_lon, off_lat) < 2.0  # always on a street
        cells.add((round(driver.lat, 3), round(driver.lon, 3)))
    assert driven > 1500 and len(cells) > 8  # actually goes places (stop signs included)


def test_no_map_falls_back_to_a_grid():
    driver = make_driver(None, *HOME, random.Random(1))
    assert isinstance(driver, GridDriver)
    assert sum(driver.step(1) for _ in range(120)) > 500


# --- generated sessions -------------------------------------------------------------


@pytest.fixture
def generated(cfg):
    paths = demo.generate(cfg, plan=SMALL_PLAN, seed=4)
    return cfg, paths


def test_generated_sessions_read_like_kismet_logs(generated):
    cfg, paths = generated
    logs = demo.root(cfg) / "logs"
    assert sorted(p.suffix for p in paths) == [".kismet", ".kismet", ".wiglecsv", ".wiglecsv"]
    lib = SessionLibrary(logs, demo.root(cfg) / "cache")
    infos = lib.sessions()
    assert len(infos) == 2  # none skipped as "still being written"
    for info in infos:
        assert info.wifi > 10 and info.located == info.wifi + info.bt
        devices = lib.devices(info.name)
        assert {d.crypt for d in devices if d.phy == "wifi"} >= {"WPA2"}
        assert all(abs(d.lat - HOME[0]) < 0.05 for d in devices)
    assert len(lib.devices(None)) < sum(i.wifi + i.bt for i in infos)  # same streets, same routers

    ups = UploadManager(cfg, State())
    ups.log_dir = logs
    assert [s.wigle_rows > 0 for s in ups.sessions()] == [True, True]

    con = sqlite3.connect(paths[0])
    assert con.execute("select kismet_version from KISMET").fetchone()[0].startswith("2025")
    assert "DEMO DATA" in con.execute("select message from messages").fetchone()[0]
    assert con.execute("select count(*) from packets").fetchone()[0] > 0
    assert con.execute("select count(*) from snapshots where snaptype = 'GPS'").fetchone()[0] > 10


def test_review_tool_finds_the_simulated_clock_step_and_refuses_wigle(generated, tmp_path, capsys):
    cfg, paths = generated
    data = tmp_path / "server"
    for db in [p for p in paths if p.suffix == ".kismet"]:
        d = data / "_demo" / db.stem
        d.mkdir(parents=True)
        for f in (db, db.with_suffix(".wiglecsv")):
            (d / (f.name + ".gz")).write_bytes(gzip.compress(f.read_bytes()))
    assert wr.all_sessions(data) == []  # _demo is never listed as a session
    sessions = wr.all_sessions(data / "_demo")
    stale = next(s for s in sessions if wr.load_kismet(s).steps)
    csv = stale.read_original()
    assert csv.is_demo and DEMO_MARKER in csv.preheader
    report = wr.run_check(stale)
    assert report.failures  # clock step and missing security, as on a real Kismet log

    assert wr.main(["--data", str(data), "--demo", "fix", stale.name]) in (0, 1)
    fixed, reviewed = stale.read_current()
    assert reviewed and any("[WPA2-PSK-CCMP]" in r.auth for r in fixed.rows)
    capsys.readouterr()
    assert wr.main(["--data", str(data), "--demo", "wigle", stale.name, "--force"]) == 1
    assert "never reach WiGLE" in capsys.readouterr().out


# --- switching -----------------------------------------------------------------------


def test_turn_on_generates_once_and_turn_off_deletes_only_demo_data(cfg, monkeypatch, tmp_path):
    monkeypatch.setattr(demo, "PLAN", SMALL_PLAN)
    real_logs = tmp_path / "logs"
    real_logs.mkdir()
    (real_logs / "real.kismet").write_text("keep me")
    cfg.log_dir = str(real_logs)

    demo.turn_on(cfg)
    assert demo.is_on(cfg)
    first = sorted(p.name for p in (demo.root(cfg) / "logs").iterdir())
    demo.turn_on(cfg)  # already has sessions: nothing regenerated
    assert sorted(p.name for p in (demo.root(cfg) / "logs").iterdir()) == first

    demo.use_demo_paths(cfg)
    assert Path(cfg.log_dir) == demo.root(cfg) / "logs"
    assert demo.turn_off(cfg) and not demo.root(cfg).exists() and not demo.is_on(cfg)
    assert (real_logs / "real.kismet").read_text() == "keep me"
    assert demo.turn_off(cfg) is False


def test_turn_off_refuses_a_directory_that_is_not_demo_data(cfg, tmp_path):
    cfg.demo.dir = str(tmp_path / "important")
    (tmp_path / "important").mkdir()
    (tmp_path / "important" / "file").write_text("x")
    with pytest.raises(RuntimeError):
        demo.turn_off(cfg)
    assert (tmp_path / "important" / "file").exists()


# --- backend and app -------------------------------------------------------------------


@pytest.fixture
def demo_backend(cfg):
    from wardrive.demo.backend import DemoBackend

    demo.root(cfg).mkdir(parents=True)
    demo.use_demo_paths(cfg)
    state = State()
    return DemoBackend(cfg, state, real_power=False, seed=2)


def wait_for(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_demo_capture_fills_the_list_and_saves_a_session(demo_backend):
    b, st = demo_backend, demo_backend.state
    b._booted -= 60  # skip the simulated GPS search
    b._update_gps(time.monotonic())
    assert st.gps.mode == 3 and abs(st.gps.lat - HOME[0]) < 0.01
    b.start_capture()
    assert wait_for(lambda: st.capture == Capture.RUNNING)
    for _ in range(240):  # two minutes of simulated driving, without waiting for it
        b._tick(time.monotonic())
    assert st.devices and st.distance_m > 200 and st.gps.speed > 0
    b.stop_capture()
    assert wait_for(lambda: st.capture == Capture.IDLE)
    assert st.summary and any(f.endswith(".wiglecsv") for f in st.summary.files)
    sessions = b.sessions.sessions()
    assert len(sessions) == 1 and sessions[0].wifi == sum(1 for d in st.devices.values() if d.phy == "wifi")


def test_demo_uploads_go_to_the_demo_folder_and_never_to_wigle(demo_backend, monkeypatch):
    b, st = demo_backend, demo_backend.state
    rec = SessionRecorder(time.time() - 600, seed=1)
    world = World(None, seed=1)
    demo.simulate(rec, GridDriver(*HOME, random.Random(1)), Scanner(world, random.Random(1)), rec.started, 60)
    rec.write(Path(b.cfg.log_dir))

    cfg = b.cfg
    cfg.upload.home.enabled, cfg.upload.home.url = True, "https://api.example.com/wardrive/upload"
    cfg.upload.wigle.enabled, cfg.upload.wigle.api_name, cfg.upload.wigle.api_token = True, "AID", "tok"
    sent, server = [], {"demo": False}

    class Resp:
        status_code = 200

        def __init__(self, body):
            self.body = body

        def json(self):
            return self.body

    def post(url, params=None, **kw):
        sent.append((url, params))
        return Resp({"ok": True, "demo": params.get("demo") == "1"})

    def get(url, params=None, **kw):
        return Resp({"ok": True, "sessions": [], **({"demo": True} if server["demo"] else {})})

    monkeypatch.setattr("wardrive.uploads.requests.post", post)
    monkeypatch.setattr("wardrive.uploads.requests.get", get)
    ups = b.uploads
    assert not ups.enabled("wigle") and ups.unavailable("wigle") == "off in demo"
    ups._run("wigle")
    assert sent == [] and "off in demo" in st.upload_status
    ups._run("home")  # an old server that would ignore demo=1: nothing is sent
    assert sent == [] and "no demo folder" in st.upload_status
    server["demo"] = True
    ups._run("home")
    assert len(sent) == 2 and all(p["demo"] == "1" for _, p in sent)
    assert json.loads((Path(cfg.state_dir) / "uploads.json").read_text())


def test_menu_demo_switch_generates_then_restarts(cfg, monkeypatch):
    from wardrive.app import App
    from wardrive.display import Display
    from wardrive.mock import MockBackend

    monkeypatch.setattr(demo, "PLAN", SMALL_PLAN)
    state = State()
    app = App(cfg, state, MockBackend(cfg, state), Display("headless"), touch=None)
    assert not app.demo
    state.capture = Capture.RUNNING
    app.toggle_demo()
    assert app.modal is not None and app.busy is None  # refuses while capturing
    app.close_modal()
    state.capture = Capture.IDLE
    app.toggle_demo()
    assert app.busy is not None
    assert wait_for(lambda: app.restart, timeout=30)
    assert not app.running and demo.is_on(cfg)


def test_status_bar_shows_demo_and_sandbox_cannot_switch(cfg, demo_backend):
    from wardrive.app import App
    from wardrive.display import Display

    app = App(cfg, demo_backend.state, demo_backend, Display("headless"), touch=None, sandbox=True)
    assert app.demo
    app.draw(time.monotonic())
    app.toggle_demo()
    assert app.busy is None and not app.restart
    shutil.rmtree(demo.root(cfg), ignore_errors=True)
