"""Saved-session browser: Kismet DB parsing, caching, merging, and the UI flow."""

import json
import os
import sqlite3
import time
from pathlib import Path

import pytest

os.environ["SDL_VIDEODRIVER"] = "dummy"

from wardrive import config  # noqa: E402
from wardrive.sessions import SessionLibrary, merge_devices, parse_kismet_db, sort_devices  # noqa: E402
from wardrive.state import State  # noqa: E402
from wardrive.touch import TouchEvent  # noqa: E402

LAT, LON = 32.61, -83.63


def wifi(mac, ssid, crypt, signal, peak=(LAT, LON), wps=False, first=1000, last=1100, packets=10):
    record = {"dot11.advertisedssid.ssid": ssid, "dot11.advertisedssid.crypt_string": crypt}
    if wps:
        record["dot11.advertisedssid.wps_state"] = 1
    sig = {"kismet.common.signal.max_signal": signal}
    if peak:
        sig["kismet.common.signal.peak_loc"] = {"kismet.common.location.geopoint": [peak[1], peak[0]]}
    device = {
        "kismet.device.base.name": ssid or mac, "kismet.device.base.crypt": crypt, "kismet.device.base.channel": "6",
        "kismet.device.base.manuf": "NETGEAR", "kismet.device.base.packets.total": packets,
        "kismet.device.base.frequency": 2437000, "kismet.device.base.signal": sig,
        "dot11.device": {"dot11.device.last_beaconed_ssid_record": record},
    }
    return ("key-" + mac, "IEEE802.11", mac, signal, 0.0, 0.0, first, last, "Wi-Fi AP", json.dumps(device))


def bt(mac, name, avg=(LAT + 0.01, LON), first=1000, last=1050):
    device = {"kismet.device.base.name": name, "kismet.device.base.manuf": "Apple, Inc.", "kismet.device.base.channel": "FHSS",
              "kismet.device.base.signal": {"kismet.common.signal.max_signal": 0}, "kismet.device.base.packets.total": 3}
    return ("key-" + mac, "Bluetooth", mac, 0, avg[0], avg[1], first, last, "BTLE", json.dumps(device))


def make_db(path: Path, rows, age: float = 3600) -> Path:
    con = sqlite3.connect(path)
    con.execute("create table devices (devkey, phyname, devmac, strongest_signal, avg_lat, avg_lon, first_time, last_time, type, device)")
    con.executemany("insert into devices values (?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    old = time.time() - age
    os.utime(path, (old, old))
    return path


def test_parse_wifi_and_bluetooth(tmp_path):
    db = make_db(tmp_path / "s1.kismet", [
        wifi("AA:00:00:00:00:01", "home", "WPA2 WPA2-PSK AES-CCMP", -48, wps=True),
        wifi("AA:00:00:00:00:02", "", "Open", -80, peak=None),  # hidden, no GPS
        bt("BB:00:00:00:00:01", "JBL Flip 5"),
        ("k", "RTL433", "x", 0, 0, 0, 0, 0, "x", "{}"),  # other phys are ignored
    ])
    info, devices = parse_kismet_db(db)
    assert (info.wifi, info.bt, info.open, info.located) == (2, 1, 1, 2)
    assert (info.start, info.end) == (1000, 1100)
    home, hidden, speaker = devices
    assert (home.name, home.crypt, home.signal, home.wps, home.lat, home.lon, home.packets) == ("home", "WPA2", -48, True, LAT, LON, 10)
    assert (hidden.label, hidden.crypt, hidden.lat) == ("<hidden>", "OPEN", 0.0)
    assert (speaker.phy, speaker.name, speaker.dev_type, speaker.lat) == ("bt", "JBL Flip 5", "BTLE", LAT + 0.01)


def test_library_skips_active_sessions_and_caches(tmp_path, monkeypatch):
    logs = tmp_path / "logs"
    logs.mkdir()
    make_db(logs / "old.kismet", [wifi("AA:00:00:00:00:01", "a", "Open", -50)], age=7200)
    make_db(logs / "done.kismet", [wifi("AA:00:00:00:00:02", "b", "Open", -50)], age=3600)
    make_db(logs / "journaled.kismet", [wifi("AA:00:00:00:00:03", "c", "Open", -50)], age=1800)
    (logs / "journaled.kismet-journal").write_bytes(b"")
    capturing = {"on": False}
    lib = SessionLibrary(logs, tmp_path / "cache", is_capturing=lambda: capturing["on"])

    assert [s.name for s in lib.sessions()] == ["done", "old"]  # newest first, journaled skipped
    (logs / "journaled.kismet").unlink()
    (logs / "journaled.kismet-journal").unlink()
    capturing["on"] = True
    assert [s.name for s in lib.sessions()] == ["old"]  # no journal: the newest file belongs to the running capture
    capturing["on"] = False

    import wardrive.sessions as sessions_mod

    calls = []
    real = sessions_mod.parse_kismet_db
    monkeypatch.setattr(sessions_mod, "parse_kismet_db", lambda p: calls.append(p.name) or real(p))
    lib.sessions()
    assert calls == []  # served from cache
    (logs / "done.kismet").unlink()  # Kismet never rewrites a finished log; simulate a replaced file
    make_db(logs / "done.kismet", [wifi("AA:00:00:00:00:02", "b", "Open", -50), bt("BB:00:00:00:00:09", "x")], age=3000)
    assert [s.bt for s in lib.sessions() if s.name == "done"] == [1] and calls == ["done.kismet"]


def test_merge_keeps_strongest_sighting(tmp_path):
    s1 = parse_kismet_db(make_db(tmp_path / "a.kismet", [
        wifi("AA:00:00:00:00:01", "cafe", "Open", -80, peak=(1.0, 1.0), first=500, last=600, packets=5),
        bt("BB:00:00:00:00:01", ""),
    ]))[1]
    s2 = parse_kismet_db(make_db(tmp_path / "b.kismet", [
        wifi("aa:00:00:00:00:01", "cafe", "Open", -55, peak=(2.0, 2.0), first=900, last=990, packets=7),
        bt("BB:00:00:00:00:01", "Tile", avg=(0, 0)),
    ]))[1]
    merged = {d.mac.upper(): d for d in merge_devices([s1, s2])}
    cafe, tile = merged["AA:00:00:00:00:01"], merged["BB:00:00:00:00:01"]
    assert (cafe.signal, cafe.lat, cafe.first_seen, cafe.last_seen, cafe.packets, cafe.sessions) == (-55, 2.0, 500, 990, 12, 2)
    assert tile.name == "Tile" and tile.lat == LAT + 0.01  # name from one, location from the other


def test_sort_devices(tmp_path):
    devices = parse_kismet_db(make_db(tmp_path / "a.kismet", [
        wifi("AA:00:00:00:00:01", "zeta", "Open", -70, first=100),
        wifi("AA:00:00:00:00:02", "", "Open", -40, first=300),
        wifi("AA:00:00:00:00:03", "Alpha", "Open", -90, first=200),
    ]))[1]
    assert [d.signal for d in sort_devices(devices, "signal")] == [-40, -70, -90]
    assert [d.label for d in sort_devices(devices, "name")] == ["Alpha", "zeta", "<hidden>"]
    assert [d.first_seen for d in sort_devices(devices, "new")] == [300, 200, 100]


# --- UI ------------------------------------------------------------------------------


@pytest.fixture
def app(tmp_path):
    from wardrive.app import App
    from wardrive.display import Display
    from wardrive.mock import MockBackend

    cfg = config.Config()
    cfg.touch.calibration_file = str(tmp_path / "touch.json")
    state = State()
    return App(cfg, state, MockBackend(cfg, state), Display("headless"), touch=None)


def tap(app, pos):
    app._dispatch(TouchEvent("down", pos, pos))
    app._dispatch(TouchEvent("up", pos, pos))


def test_menu_to_session_to_network_detail_and_back(app):
    app.show("menu")
    sessions_button = next(b for b in app.current.buttons if b.label == "SESSIONS")
    tap(app, sessions_button.rect.center)
    view = app.current
    assert view.name == "sessions"
    view.loader.wait()
    assert len(view.entries()) == 4  # "All sessions" + 3 mock drives
    app.draw(time.monotonic())
    assert app.nav[-1].active()  # MENU stays highlighted

    from wardrive.views.sessions import SESSION_LIST, SESSION_ROW_H

    tap(app, (100, SESSION_LIST.y + SESSION_ROW_H + 10))  # first real session
    nets = app.current
    assert nets.name == "session_nets" and nets.session == "wardrive-mock-3"
    nets.loader.wait()
    rows = nets.rows()
    known = [d.signal for d in rows if d.signal]
    assert len(rows) == 260 and known == sorted(known, reverse=True)  # strongest first
    assert all(d.signal == 0 for d in rows[len(known):])  # no reading sorts last
    app.draw(time.monotonic())

    tap(app, (100, nets.list.rect.y + 5))
    assert app.modal is not None and app.modal.device is rows[0]
    app.draw(time.monotonic())
    tap(app, (10, 10))
    assert app.modal is None

    filter_button = nets.buttons[1]
    tap(app, filter_button.rect.center)
    tap(app, filter_button.rect.center)
    assert all(d.phy == "bt" for d in nets.rows())

    tap(app, nets.buttons[0].rect.center)  # BACK
    assert app.current.name == "sessions"


def test_all_sessions_row_shows_merged_devices(app):
    app.show("sessions")
    app.current.loader.wait()
    from wardrive.views.sessions import SESSION_LIST

    tap(app, (100, SESSION_LIST.y + 10))
    nets = app.current
    nets.loader.wait()
    assert nets.session is None and nets.title == "All sessions"
    assert any(d.sessions == 3 for d in nets.rows())


def test_no_sessions_message(app, monkeypatch):
    monkeypatch.setattr(app.backend.sessions, "sessions", lambda: [])
    app.show("sessions")
    app.current.loader.wait()
    assert app.current.entries() == []
    app.draw(time.monotonic())
