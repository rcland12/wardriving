"""Tests for tools/wardrive_review.py using a synthetic session (no network, no real data)."""

import gzip
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import wardrive_review as wr  # noqa: E402

PRE = "WigleWifi-1.4,appRelease=Kismet2025090,model=Kismet,release=2025.09.0,device=kismet,display=kismet,board=kismet,brand=kismet"
HDR = "MAC,SSID,AuthMode,FirstSeen,Channel,RSSI,CurrentLatitude,CurrentLongitude,AltitudeMeters,AccuracyMeters,Type"
STEP = 27961  # seconds the Pi's clock was behind
STALE0 = datetime(2026, 9, 12, 19, 55, 21, tzinfo=timezone.utc)
TRUE0 = STALE0 + timedelta(seconds=STEP)

NETWORKS = {  # BSSID -> (SSID, Kismet crypt string, advertises WPS)
    "AA:00:00:00:00:01": ("home", "WPA2 WPA2-PSK AES-CCMP", True),
    "AA:00:00:00:00:02": ("corp", "WPA2 WPA2-EAP AES-CCMP", False),
    "AA:00:00:00:00:03": ("cafe", "Open", False),
    "AA:00:00:00:00:04": ("new,router", "WPA3 WPA3-PSK WPA3-SAE AES-CCMP", False),
    "AA:00:00:00:00:05": ("old", "WPA1 WPA1-PSK TKIP", False),
}


def row(mac, first_seen, auth="[WPS][ESS]", kind="WIFI", rssi=-60):
    ssid = NETWORKS.get(mac, ("",))[0]
    return f"{mac},{ssid},{auth},{first_seen:%Y-%m-%d %H:%M:%S},6,{rssi},32.58,-83.64,110.0,0,{kind}"


def make_session(root: Path, name="wardrive-20260912-19-55-21-1", with_db=True) -> wr.Session:
    """Mimic the real bug pattern: stale and correct FirstSeen values interleave."""
    d = root / name
    d.mkdir(parents=True)
    lines = [
        row("AA:00:00:00:00:01", STALE0),
        row("AA:00:00:00:00:02", STALE0 + timedelta(seconds=3)),
        row("AA:00:00:00:00:03", STALE0 + timedelta(seconds=5), auth="[ESS]"),
        row("AA:00:00:00:00:04", TRUE0 + timedelta(seconds=60)),  # after the clock was set
        row("AA:00:00:00:00:01", STALE0, rssi=-52),  # older device, rewritten later: keeps stale FirstSeen
        row("AA:00:00:00:00:05", TRUE0 + timedelta(seconds=70)),
        row("AA:00:00:00:00:05", TRUE0 + timedelta(seconds=70)),  # exact duplicate
        row("BB:00:00:00:00:09", TRUE0 + timedelta(seconds=80), auth="Misc [LE]", kind="BLE"),
    ]
    (d / f"{name}.wiglecsv.gz").write_bytes(gzip.compress(("\n".join([PRE, HDR, *lines]) + "\n").encode()))
    if with_db:
        db = d / f"{name}.kismet"
        con = sqlite3.connect(db)
        con.execute("create table devices (devmac text, phyname text, device text)")
        con.execute("create table packets (ts_sec integer, ts_usec integer)")
        for mac, (ssid, crypt, wps) in NETWORKS.items():
            record = {"dot11.advertisedssid.ssid": ssid, "dot11.advertisedssid.crypt_string": crypt}
            if wps:
                record["dot11.advertisedssid.wps_state"] = 1
            blob = json.dumps({"dot11.device": {"dot11.device.last_beaconed_ssid_record": record}})
            con.execute("insert into devices values (?, 'IEEE802.11', ?)", (mac, blob))
        # Packets 0.2 s apart; at the step one packet is logged out of order (+X, -X, +X).
        base = STALE0.timestamp()
        ts = [base + i * 0.2 for i in range(50)]
        true = [base + 50 * 0.2 + STEP + i * 0.2 for i in range(50)]
        ts = ts[:-1] + [true[0], ts[-1]] + true[1:]
        con.executemany("insert into packets values (?, ?)", [(int(t), int((t % 1) * 1e6)) for t in ts])
        con.commit()
        con.close()
        (d / f"{name}.kismet.gz").write_bytes(gzip.compress(db.read_bytes()))
        db.unlink()
    return wr.Session(d)


def test_row_parse_handles_commas_in_ssid_and_round_trips():
    line = row("AA:00:00:00:00:04", TRUE0)
    r = wr.Row.parse(line)
    assert r.ssid == "new,router" and r.type == "WIFI" and r.line() == line
    assert wr.Row.parse("too,few,fields") is None


@pytest.mark.parametrize(
    "crypt,wps,expected",
    [
        ("WPA2 WPA2-PSK AES-CCMP", False, "[WPA2-PSK-CCMP][RSN-PSK-CCMP][ESS]"),
        ("WPA2 WPA2-PSK AES-CCMP", True, "[WPA2-PSK-CCMP][RSN-PSK-CCMP][WPS][ESS]"),
        ("WPA2 WPA2-EAP AES-CCMP", False, "[WPA2-EAP-CCMP][RSN-EAP-CCMP][ESS]"),
        ("WPA3 WPA3-PSK WPA3-SAE AES-CCMP", False, "[WPA3-PSK+SAE-CCMP][RSN-PSK+SAE-CCMP][ESS]"),
        ("WPA2 WPA2-PSK TKIP AES-CCMP", False, "[WPA2-PSK-CCMP+TKIP][RSN-PSK-CCMP+TKIP][ESS]"),
        ("WPA1 WPA1-PSK TKIP", False, "[WPA-PSK-TKIP][ESS]"),
        ("WEP", False, "[WEP][ESS]"),
        ("Open", False, "[ESS]"),
        ("Open", True, "[WPS][ESS]"),
    ],
)
def test_wigle_authmode(crypt, wps, expected):
    assert wr.wigle_authmode(crypt, wps) == expected


def test_packet_steps_merge_out_of_order_logging():
    base = 1_000_000.0
    ts = [base, base + 0.2, base + 0.4 + STEP, base + 0.3, base + 0.6 + STEP, base + 0.8 + STEP]
    steps = wr.packet_clock_steps(ts)
    assert len(steps) == 1 and abs(steps[0][2] - STEP) < 1


def test_quiet_gap_without_packet_jump_is_not_a_clock_step(tmp_path):
    """A long stretch with no new networks leaves a FirstSeen gap but no packet-order jump."""
    s = make_session(tmp_path)
    info = wr.load_kismet(s)
    info.steps = []
    assert wr.find_clock_steps(s.read_original().rows, info) == []


def test_check_finds_clock_step_security_and_duplicates(tmp_path):
    s = make_session(tmp_path)
    rep = wr.run_check(s)
    assert rep.verdict == "FAIL"
    clock = [f for f in rep.failures if "clock was changed" in f]
    assert len(clock) == 1 and "4 rows" in clock[0] and "+7h46m01s" in clock[0] and "packet timestamps" in clock[0]
    assert any("different security" in f for f in rep.failures)
    assert any("duplicate" in w for w in rep.warnings)


def test_fix_repairs_everything_and_leaves_originals_untouched(tmp_path, capsys):
    s = make_session(tmp_path)
    original = s.original_csv.read_bytes()
    assert wr.main(["--data", str(tmp_path), "fix", "latest"]) == 0
    assert s.original_csv.read_bytes() == original

    csv, reviewed = s.read_current()
    assert reviewed and len(csv.rows) == 7  # one duplicate removed
    times = sorted(r.time for r in csv.rows)
    assert times[0] == TRUE0 - timedelta(seconds=0) and times[-1] == TRUE0 + timedelta(seconds=80)
    auth = {r.mac: r.auth for r in csv.rows}
    assert auth["AA:00:00:00:00:01"] == "[WPA2-PSK-CCMP][RSN-PSK-CCMP][WPS][ESS]"
    assert auth["AA:00:00:00:00:02"] == "[WPA2-EAP-CCMP][RSN-EAP-CCMP][ESS]"  # bogus WPS flag dropped
    assert auth["AA:00:00:00:00:03"] == "[ESS]"
    assert auth["BB:00:00:00:00:09"] == "Misc [LE]"  # BLE untouched
    assert "new,router" in s.reviewed_csv.read_text()

    record = s.record()["reviewed"]
    assert len(record["fixes"]) == 3 and record["rows"] == 7
    assert wr.run_check(s).verdict == "OK"


def test_fix_can_drop_stale_rows_instead(tmp_path):
    s = make_session(tmp_path)
    wr.main(["--data", str(tmp_path), "fix", "latest", "--drop-stale"])
    csv, _ = s.read_current()
    assert all(r.time >= TRUE0 for r in csv.rows) and len(csv.rows) == 3


def test_without_kismet_db_offset_comes_from_csv_gap(tmp_path):
    s = make_session(tmp_path, with_db=False)
    rep = wr.run_check(s)
    assert any("gap between FirstSeen clusters" in f for f in rep.failures)


def test_latest_uses_upload_time_not_session_name(tmp_path):
    older_name_newer_upload = make_session(tmp_path, "wardrive-20260912-19-55-21-1")
    newer_name_older_upload = make_session(tmp_path, "wardrive-20260912-21-35-42-1")
    for f in newer_name_older_upload.path.iterdir():
        os.utime(f, (1_000_000, 1_000_000))
    assert wr.find_session(tmp_path, "latest").name == older_name_newer_upload.name
    with pytest.raises(SystemExit):
        wr.find_session(tmp_path, "wardrive-2026")  # ambiguous


def test_wigle_upload_requires_passing_check_and_records_transaction(tmp_path, monkeypatch, capsys):
    s = make_session(tmp_path)
    monkeypatch.setenv("WIGLE_API_NAME", "AIDtest")
    monkeypatch.setenv("WIGLE_API_TOKEN", "secret")
    monkeypatch.setenv("WIGLE_DONATE", "on")
    sent = {}

    class Resp:
        def __init__(self, body):
            self.body = body

        def read(self):
            return json.dumps(self.body).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout):
        sent.update(url=req.full_url, auth=req.get_header("Authorization"), ctype=req.get_header("Content-type"), body=req.data)
        return Resp({"success": True, "results": {"transids": [{"transId": "20260913-00001"}]}})

    monkeypatch.setattr(wr.urllib.request, "urlopen", fake_urlopen)

    assert wr.main(["--data", str(tmp_path), "wigle", "latest"]) == 1  # unreviewed session fails checks
    assert not sent

    wr.main(["--data", str(tmp_path), "fix", "latest"])
    assert wr.main(["--data", str(tmp_path), "wigle", "latest"]) == 0
    assert sent["url"].endswith("/file/upload") and sent["auth"].startswith("Basic ")
    assert b'name="donate"\r\n\r\non' in sent["body"] and b"[WPA2-EAP-CCMP]" in sent["body"]
    assert s.record()["wigle"]["transids"] == ["20260913-00001"]

    sent.clear()
    assert wr.main(["--data", str(tmp_path), "wigle", "latest"]) == 1  # no accidental re-upload
    assert not sent


def test_env_file_does_not_override_environment(tmp_path, monkeypatch):
    env = tmp_path / "review.env"
    env.write_text("# comment\nWARDRIVE_DATA=/from/file\nWIGLE_API_NAME='AIDfile'\n")
    env.chmod(0o600)
    monkeypatch.setenv("WARDRIVE_DATA", "/from/env")
    monkeypatch.delenv("WIGLE_API_NAME", raising=False)
    wr.load_env_file(env)
    assert os.environ["WARDRIVE_DATA"] == "/from/env" and os.environ["WIGLE_API_NAME"] == "AIDfile"
