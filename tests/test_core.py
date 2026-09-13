"""Hardware-free tests: calibration math, parsing, and UI dispatch against the mock backend."""

import json
import os
import time

import pytest

os.environ["SDL_VIDEODRIVER"] = "dummy"

from wardrive import config
from wardrive.kismet import KismetClient, read_auth
from wardrive.state import Capture, State, classify_crypt
from wardrive.touch import Calibration, TouchEvent
from wardrive.uploads import LogSession, UploadManager

TARGETS = [(30, 30), (450, 30), (450, 290), (30, 290)]


def test_calibration_recovers_rotated_swapped_mapping():
    # Panel mounted 180° with X/Y swapped: raw x drives screen y, both inverted.
    def raw_for(sx, sy):
        return (3900 - sy * 3700 / 320, 3900 - sx * 3700 / 480)

    raws = [raw_for(*t) for t in TARGETS]
    cal = Calibration.fit(raws, TARGETS)
    for sx, sy in [(240, 160), (10, 300), (470, 5)]:
        mx, my = cal.map(*raw_for(sx, sy))
        assert abs(mx - sx) <= 1 and abs(my - sy) <= 1


def test_calibration_rejects_degenerate_points():
    with pytest.raises(ValueError):
        Calibration.fit([(100, 100)] * 4, TARGETS)


def test_calibration_roundtrip(tmp_path):
    cal = Calibration((0.1, 0.0, -5, 0.0, 0.2, 3))
    path = tmp_path / "touch.json"
    cal.save(path)
    assert Calibration.load(path).coeffs == cal.coeffs
    assert Calibration.load(tmp_path / "missing.json") is None


@pytest.mark.parametrize(
    "raw,phy,expected",
    [
        ("WPA3 WPA3-PSK WPA3-SAE AES-CCMP", "wifi", "WPA3"),
        ("WPA2 WPA3-SAE AES-CCMP", "wifi", "WPA3"),
        ("WPA2 WPA2-PSK AES-CCMP", "wifi", "WPA2"),
        ("WPA TKIP", "wifi", "WPA"),
        ("WEP", "wifi", "WEP"),
        ("Open", "wifi", "OPEN"),
        ("", "wifi", "OPEN"),
        ("", "bt", "BT"),
    ],
)
def test_classify_crypt(raw, phy, expected):
    assert classify_crypt(raw, phy) == expected


def test_read_auth(tmp_path):
    f = tmp_path / "kismet_httpd.conf"
    f.write_text("httpd_username=wardrive\nhttpd_password=abc=123\n")
    assert read_auth(str(f)) == ("wardrive", "abc=123")
    assert read_auth(str(tmp_path / "nope")) is None


def test_kismet_device_parsing(monkeypatch):
    rows = [
        {  # hidden Wi-Fi AP: name is the MAC, ssid empty
            "kismet.device.base.key": "k1", "kismet.device.base.macaddr": "3A:BC:F5:51:8F:B1",
            "kismet.device.base.phyname": "IEEE802.11", "kismet.device.base.name": "3A:BC:F5:51:8F:B1",
            "kismet.device.base.channel": "1", "kismet.device.base.crypt": "WPA3 WPA3-SAE AES-CCMP",
            "kismet.device.base.manuf": "MediaTek Inc", "kismet.device.base.first_time": 1, "kismet.device.base.last_time": 2,
            "signal": -69, "ssid": "", "geopoint": [-78.6, 35.7],
        },
        {  # BLE: absent fields come back as 0
            "kismet.device.base.key": "k2", "kismet.device.base.macaddr": "28:E6:A9:E6:54:63",
            "kismet.device.base.phyname": "Bluetooth", "kismet.device.base.name": "",
            "kismet.device.base.channel": "FHSS", "kismet.device.base.crypt": "",
            "kismet.device.base.manuf": "Samsung", "kismet.device.base.first_time": 1, "kismet.device.base.last_time": 2,
            "signal": 0, "ssid": 0, "geopoint": 0,
        },
        {"kismet.device.base.key": "k3", "kismet.device.base.phyname": "RTL433"},
    ]
    client = KismetClient("http://x", "/nonexistent")
    monkeypatch.setattr(client, "_request", lambda *a, **k: rows)
    wifi, bt = client.devices_since(-5)
    assert (wifi.phy, wifi.name, wifi.label, wifi.crypt, wifi.lat, wifi.lon) == ("wifi", "", "<hidden>", "WPA3", 35.7, -78.6)
    assert (bt.phy, bt.label, bt.crypt, bt.lat) == ("bt", "28:E6:A9:E6:54:63", "BT", 0.0)


def test_upload_sessions_skip_active_and_empty(tmp_path, monkeypatch):
    cfg = config.Config(log_dir=str(tmp_path / "logs"), state_dir=str(tmp_path / "state"))
    logs = tmp_path / "logs"
    logs.mkdir()
    old = time.time() - 3600
    header = "WigleWifi-1.4,appRelease=x\nMAC,SSID,AuthMode,FirstSeen,Channel,RSSI,CurrentLatitude\n"
    for name, rows in [("wardrive-20260912-20-04-41-1", 3), ("wardrive-20260912-21-00-00-1", 0)]:
        (logs / f"{name}.kismet").write_bytes(b"x" * 10)
        (logs / f"{name}.wiglecsv").write_text(header + "row\n" * rows)
        for f in logs.glob(f"{name}.*"):
            os.utime(f, (old, old))
    (logs / "wardrive-20260912-22-00-00-1.kismet").write_bytes(b"live")  # just written: active

    cfg.upload.wigle.enabled, cfg.upload.wigle.api_name, cfg.upload.wigle.api_token = True, "n", "t"
    um = UploadManager(cfg, State())
    names = [s.name for s in um.sessions()]
    assert names == ["wardrive-20260912-21-00-00-1", "wardrive-20260912-20-04-41-1"]
    assert [s.name for s in um.pending("wigle")] == ["wardrive-20260912-20-04-41-1"]  # empty CSV skipped

    monkeypatch.setattr(um, "_wigle", lambda s: None)
    um._run("wigle")
    assert um.uploaded("wardrive-20260912-20-04-41-1", "wigle")
    assert um.pending("wigle") == []
    assert json.loads((tmp_path / "state" / "uploads.json").read_text())


def test_gps_messages_update_state():
    from wardrive.gps import GpsClient

    state = State()
    state.gps.gpsd_up = True
    state.capture = Capture.RUNNING
    client = GpsClient(state, "localhost", 2947)
    client._on_message(b'{"class":"SKY","nSat":14,"uSat":9,"hdop":0.9,"satellites":[]}')
    client._on_message(b'{"class":"TPV","mode":3,"lat":35.0,"lon":-78.0,"altHAE":90.5,"speed":10.0,"time":"2026-09-12T20:00:00.000Z"}')
    client._on_message(b'{"class":"TPV","mode":3,"lat":35.001,"lon":-78.0,"speed":10.0}')
    client._on_message(b"not json")
    g = state.gps
    assert g.has_fix and (g.sats_used, g.sats_seen, g.alt) == (9, 14, 90.5)
    assert g.time_valid and g.searching_since == 0.0
    assert 100 < state.distance_m < 120  # 0.001° latitude ≈ 111 m
    assert any("GPS fix acquired" in e.text for e in state.events)


def test_gps_cold_start_diagnosis():
    from wardrive.gps import GpsClient
    from wardrive.views.gps import diagnosis

    state = State()
    state.gps.gpsd_up = True
    client = GpsClient(state, "localhost", 2947)
    # What the BU-353N sends indoors after a cold start: epoch time, no fix, no sky.
    client._on_message(b'{"class":"TPV","mode":1,"time":"1980-01-06T00:07:24.012Z"}')
    g = state.gps
    assert not g.has_fix and not g.time_valid and g.searching_since > 0
    assert "no satellites" in diagnosis(g)[0]
    client._on_message(b'{"class":"SKY","nSat":3,"uSat":0,"satellites":[{"PRN":5,"ss":31,"used":false},{"PRN":9,"ss":18,"used":false},{"PRN":2,"ss":0,"used":false}]}')
    assert [s[0] for s in g.satellites] == [5, 9, 2]
    assert diagnosis(g)[0].startswith("Cold start")


def _backend_with_fake_clock(monkeypatch, sources):
    """A real Backend whose clock reads come from `sources` and whose systemctl is stubbed."""
    import wardrive.backend as backend_mod

    readings = iter(sources)
    last = {"v": sources[-1]}

    def fake_clock():
        last["v"] = next(readings, last["v"])
        return last["v"]

    calls = []
    monkeypatch.setattr(backend_mod, "clock_source", fake_clock)
    monkeypatch.setattr(backend_mod, "_sudo_systemctl", lambda *a, timeout: (calls.append(a) or (True, "")))
    state = State()
    b = backend_mod.Backend(config.Config(), state)
    monkeypatch.setattr(b.kismet, "status", lambda: {"kismet.system.timestamp.sec": 1})
    return b, state, calls


def _wait_until(pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_start_waits_for_clock_then_captures(monkeypatch):
    b, state, calls = _backend_with_fake_clock(monkeypatch, ["unsynced", "unsynced", "GPS"])
    b.start_capture()
    assert state.capture in (Capture.STARTING, Capture.WAITING)  # set synchronously: no double start
    assert _wait_until(lambda: state.capture == Capture.WAITING)
    assert not calls  # Kismet not started while the clock is wrong
    assert _wait_until(lambda: state.capture == Capture.RUNNING)
    assert calls == [("start", "wardrive-kismet.service")]
    assert any("Clock set from GPS" in e.text for e in state.events)


def test_cancel_while_waiting_for_clock(monkeypatch):
    b, state, calls = _backend_with_fake_clock(monkeypatch, ["unsynced"])
    b.start_capture()
    assert _wait_until(lambda: state.capture == Capture.WAITING)
    b.stop_capture()
    assert _wait_until(lambda: state.capture == Capture.IDLE)
    assert not calls


def test_synced_clock_or_disabled_wait_starts_immediately(monkeypatch):
    b, state, calls = _backend_with_fake_clock(monkeypatch, ["NTP"])
    b.start_capture()
    assert _wait_until(lambda: state.capture == Capture.RUNNING)
    b, state, calls = _backend_with_fake_clock(monkeypatch, ["unsynced"])
    b.cfg.capture.wait_for_clock = False
    b.start_capture()
    assert _wait_until(lambda: state.capture == Capture.RUNNING)


def test_log_session_started_is_local_time():
    s = LogSession("wardrive-20260912-20-04-41-1")
    assert len(s.started) == len("09/12 16:04")


def test_config_loads_nested_tables(tmp_path):
    p = tmp_path / "w.toml"
    p.write_text('units = "metric"\n[display]\nrotate = 0\n[upload.home]\nenabled = true\nurl = "http://h/u"\n[bogus]\nx=1\n')
    cfg = config.load(p)
    assert cfg.units == "metric" and cfg.display.rotate == 0
    assert cfg.upload.home.enabled and cfg.upload.home.url == "http://h/u"


def test_config_home_headers_and_example_file(tmp_path, monkeypatch):
    from pathlib import Path

    p = tmp_path / "w.toml"
    p.write_text('[upload.home]\nurl = "https://x/y"\n[upload.home.headers]\nCF-Access-Client-Id = "a.access"\nCF-Access-Client-Secret = "s"\n')
    cfg = config.load(p)
    assert cfg.upload.home.headers == {"CF-Access-Client-Id": "a.access", "CF-Access-Client-Secret": "s"}

    import gzip
    import hashlib

    import wardrive.uploads as uploads

    sent = {}

    class Resp:
        def __init__(self, status, body):
            self.status_code, self._body = status, body

        def json(self):
            if self._body is None:
                raise ValueError("not json")
            return self._body

    reply = Resp(200, {"ok": True})

    def fake_post(url, params, headers, data, timeout, allow_redirects):
        sent.update(url=url, params=params, headers=headers, payload=data.read(), redirects=allow_redirects)
        return reply

    monkeypatch.setattr(uploads.requests, "post", fake_post)
    cfg.state_dir = str(tmp_path / "state")
    f = tmp_path / "s.wiglecsv"
    f.write_text("MAC,SSID\n" * 50)
    manager = UploadManager(cfg, State())
    manager._home(LogSession("s", files=[f]))
    assert sent["headers"]["CF-Access-Client-Secret"] == "s" and "Authorization" not in sent["headers"]
    assert sent["params"] == {"session": "s", "file": "s.wiglecsv.gz"} and sent["redirects"] is False
    assert gzip.decompress(sent["payload"]) == f.read_bytes()
    assert sent["headers"]["X-Content-SHA256"] == hashlib.sha256(sent["payload"]).hexdigest()

    # Cloudflare Access rejecting the token shows up as a redirect or an HTML page.
    reply = Resp(302, None)
    with pytest.raises(RuntimeError, match="Cloudflare Access"):
        manager._home(LogSession("s", files=[f]))
    reply = Resp(409, {"ok": False, "error": "already exists with different contents"})
    with pytest.raises(RuntimeError, match="409.*different contents"):
        manager._home(LogSession("s", files=[f]))

    # The shipped example must parse and stay disabled with empty secrets.
    example = config.load(Path(__file__).parent.parent / "config" / "wardrive.toml.example")
    assert not example.upload.home.enabled and example.upload.home.headers == {}
    assert example.upload.wigle.api_token == ""


def test_fbdev_present_rotates_scales_and_writes_native_format():
    import pygame

    from wardrive.display import Display, FrameBuffer

    class FakeFB(FrameBuffer):
        def __init__(self):  # 2x the canvas, RGB565 like the Pi's vc4drmfb
            self.width, self.height, self.bpp, self.stride = 960, 640, 16, 1920
            self.masks = (0xF800, 0x07E0, 0x001F, 0)
            self.written = None

        def write(self, frame):
            self.written = frame.copy()

    display = Display("headless")
    display.fb, display.rotate = FakeFB(), 180
    display.canvas = display.fb.surface((480, 320))
    display._out = display.fb.surface((960, 640))
    display.screen_size = (960, 640)

    display.canvas.fill((0, 0, 0))
    display.canvas.fill((255, 0, 0), pygame.Rect(0, 0, 10, 10))  # red block, top-left
    display.present()
    out = display.fb.written
    assert out.get_bitsize() == 16
    assert out.get_at((955, 635))[:3] == (255, 0, 0)  # 180°: now bottom-right, 2x scaled
    assert out.get_at((5, 5))[:3] == (0, 0, 0)


# --- UI dispatch -------------------------------------------------------------------


@pytest.fixture
def app(tmp_path):
    from wardrive.app import App
    from wardrive.display import Display
    from wardrive.mock import MockBackend

    cfg = config.Config()
    cfg.touch.calibration_file = str(tmp_path / "touch.json")
    state = State()
    backend = MockBackend(cfg, state)
    return App(cfg, state, backend, Display("headless"), touch=None)


def tap(app, pos):
    app._dispatch(TouchEvent("down", pos, pos))
    app._dispatch(TouchEvent("up", pos, pos))


def test_start_button_starts_capture(app, monkeypatch):
    calls = []
    monkeypatch.setattr(app.backend, "start_capture", lambda: calls.append("start"))
    tap(app, app.nav[0].rect.center)
    assert calls == ["start"]


def test_nav_switches_views(app):
    for button, name in zip(app.nav[1:], ["nets", "stats", "gps", "log", "menu"]):
        tap(app, button.rect.center)
        assert app.current.name == name
        app.draw(time.monotonic())  # every view renders without error


def test_waiting_state_renders_and_cancel_button_works(app, monkeypatch):
    calls = []
    monkeypatch.setattr(app.backend, "stop_capture", lambda: calls.append("stop"))
    app.state.capture = Capture.WAITING
    app.state.sys.clock_source = "unsynced"
    for name in ("nets", "stats", "gps", "log", "menu"):
        app.show(name)
        app.draw(time.monotonic())
    assert app.nav[0].label() == "CANCEL" and app.nav[0].is_enabled()
    tap(app, app.nav[0].rect.center)
    assert calls == ["stop"]


def test_shutdown_requires_hold(app, monkeypatch):
    calls = []
    monkeypatch.setattr(app.backend, "poweroff", lambda: calls.append("off"))
    app.show("menu")
    shutdown = next(b for b in app.current.buttons if b.label == "SHUTDOWN")
    tap(app, shutdown.rect.center)
    app.current.update(time.monotonic())
    assert calls == []  # a quick tap does nothing
    app._dispatch(TouchEvent("down", shutdown.rect.center, shutdown.rect.center))
    app.current.update(time.monotonic() + 2.1)
    assert calls == ["off"] and app.shutting_down


def test_calibration_flow_saves_and_maps(app):
    app.show("calibrate")
    view = app.current
    for t in TARGETS:
        tap(app, t)
    assert view.phase == "verify"
    tap(app, (240, 160))
    assert view.phase == "test"
    assert app.cfg.calibration_path.exists()
    assert app.calibration.map(100, 100) == (100, 100)


def test_row_tap_opens_detail_modal(app):
    app.backend.start()
    app.backend.prefill(20)
    app.show("nets")
    view = app.current
    view._cache_time = 0
    row_y = view.scroll.rect.y + 5
    tap(app, (100, row_y))
    assert app.modal is not None
    tap(app, (10, 10))
    assert app.modal is None
    assert app.state.capture == Capture.RUNNING
