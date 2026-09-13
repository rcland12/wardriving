"""Entry point: python3 -m wardrive [--mock] [--window | --screenshot DIR]"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import tempfile
from pathlib import Path

from . import config, demo
from .state import State


def main() -> int:
    p = argparse.ArgumentParser(
        prog="wardrive", description="Touch UI for the wardriver"
    )
    p.add_argument("--config", help="path to wardrive.toml")
    p.add_argument(
        "--mock",
        action="store_true",
        default=bool(os.environ.get("WARDRIVE_MOCK")),
        help="demo mode with its own data under ~/.local/state/wardrive (env WARDRIVE_MOCK=1)",
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--window",
        action="store_true",
        help="run in a desktop window instead of fullscreen",
    )
    mode.add_argument(
        "--screenshot",
        metavar="DIR",
        help="render every screen to PNG and exit (headless)",
    )
    mode.add_argument(
        "--test-upload",
        action="store_true",
        help="check WiGLE / home server credentials without uploading, then exit",
    )
    p.add_argument(
        "--scale", type=int, default=2, help="window scale factor (default 2)"
    )
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = config.load(args.config)
    state = State()

    if args.test_upload:
        from .uploads import UploadManager

        lines = UploadManager(cfg, state).test_connections()
        print("\n".join(lines))
        return 0 if all(": OK" in line or "not configured" in line for line in lines) else 1

    # Imported late so SDL env vars are set before pygame initializes.
    from .app import App
    from .display import Display

    if args.screenshot:
        from .demo.backend import DemoBackend

        with tempfile.TemporaryDirectory(prefix="wardrive-screenshots-") as tmp:
            cfg.demo.dir = str(Path(tmp) / "demo")
            demo.turn_on(cfg, seed=7)
            demo.use_demo_paths(cfg)
            display = Display("headless")
            backend = DemoBackend(cfg, state, real_power=False, seed=7)
            backend.start()
            backend.prefill()
            app = App(cfg, state, backend, display, touch=None)
            for path in app.screenshots(Path(args.screenshot)):
                print(path)
        return 0

    if args.mock or demo.is_on(cfg):
        from .demo.backend import DemoBackend

        if args.mock:  # a desktop sandbox, separate from the Pi's demo directory
            cfg.demo.dir = str(cfg.state_path / "mock-demo")
            if not demo.is_on(cfg):
                print("creating demo sessions…", flush=True)
                demo.turn_on(cfg, progress=lambda i, n, name: print(f"  {i}/{n} {name}", flush=True))
        demo.use_demo_paths(cfg)
        backend = DemoBackend(cfg, state, real_power=not args.mock)
    else:
        from .backend import Backend

        backend = Backend(cfg, state)

    if args.window:
        display, touch = Display("window", scale=args.scale), None
    else:
        from .touch import EvdevTouch

        display = Display(cfg.display.backend, rotate=cfg.display.rotate)
        touch = EvdevTouch(cfg.touch.device_name)
        touch.start()

    backend.start()
    app = App(cfg, state, backend, display, touch, sandbox=args.mock)

    def stop(*_):
        app.running = False

    def snapshot(*_):
        app.snapshot_requested = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    signal.signal(
        signal.SIGUSR1, snapshot
    )  # systemctl kill -s USR1 wardrive-ui
    try:
        app.run()
    finally:
        display.close()
    if app.restart:  # demo mode switched: start over with the other backend
        if os.environ.get("INVOCATION_ID"):  # under systemd: exit, Restart=always starts us again
            logging.info("exiting so systemd restarts the UI")
            return 75
        logging.info("restarting")
        os.execv(sys.executable, [sys.executable, "-m", "wardrive", *sys.argv[1:]])
    return 0


if __name__ == "__main__":
    sys.exit(main())
