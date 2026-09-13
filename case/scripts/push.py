#!/usr/bin/env python3
"""Push fusion_build.py stages straight to the Fusion add-in. No hand-pasting.

    python scripts/push.py status                 # add-in health check
    python scripts/push.py info                   # active doc, body count
    python scripts/push.py newdoc params 01 02    # run stages in order
    python scripts/push.py --all                  # params + 01..06 + verify
    python scripts/push.py cmd save_as '{"name": "wardriving-case-clean"}'

Stops at the first stage that errors. Every push is appended to
logs/build_log.jsonl with a timestamp and the sha256 of the exact code sent,
so any model state can be traced back to the script revision that made it.
"""
import hashlib, json, os, pathlib, sys, time, urllib.error, urllib.request

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fusion_build as fb  # noqa: E402

URL = os.environ.get("FUSION_BRIDGE_URL", "http://127.0.0.1:7432/")
LOG = HERE.parent / "logs" / "build_log.jsonl"


def post(payload, timeout=300):
    req = urllib.request.Request(URL, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
    except urllib.error.URLError as e:
        return {"error": "unreachable: %s" % e}, time.time() - t0
    try:
        return json.loads(raw), time.time() - t0
    except ValueError:
        return {"error": "non-JSON reply", "raw": raw[:500]}, time.time() - t0


def log(entry):
    LOG.parent.mkdir(exist_ok=True)
    with LOG.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def failed(resp):
    if "error" in resp:
        return True
    out = resp.get("output")
    return isinstance(out, dict) and "ERROR" in out


def run_stage(stage):
    code = fb.build_code(stage)
    sha = hashlib.sha256(code.encode()).hexdigest()[:12]
    resp, dt = post({"command": "execute_script", "params": {"code": code}})
    log({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "stage": stage, "sha": sha,
         "secs": round(dt, 1), "ok": not failed(resp), "resp": resp})
    print("[%s] sha=%s %.1fs -> %s" % (stage, sha, dt, json.dumps(resp.get("output", resp))))
    return not failed(resp)


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__); return 0
    if argv[0] == "status":
        resp, dt = post({"command": "fusion_status"}, timeout=40)
        print("%.1fs %s" % (dt, json.dumps(resp))); return 0 if "error" not in resp else 1
    if argv[0] == "cmd":
        params = json.loads(argv[2]) if len(argv) > 2 else None
        resp, dt = post({"command": argv[1], "params": params})
        log({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "cmd": argv[1], "params": params,
             "secs": round(dt, 1), "resp": resp})
        print("%.1fs %s" % (dt, json.dumps(resp))); return 0 if "error" not in resp else 1
    stages = ["params"] + fb.ORDER if argv[0] == "--all" else argv
    for s in stages:
        if not run_stage(s):
            print("STOPPED at stage %s" % s); return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
