"""Entry point: python3 -m wardrive [--mock] [--window | --screenshot DIR]"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
from pathlib import Path

from . import config
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
        help="simulated Kismet/GPS backend (env WARDRIVE_MOCK=1)",
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
        from .mock import MockBackend

        display = Display("headless")
        backend = MockBackend(cfg, state)
        backend.start()
        backend.prefill()
        app = App(cfg, state, backend, display, touch=None)
        for path in app.screenshots(Path(args.screenshot)):
            print(path)
        return 0

    if args.mock:
        from .mock import MockBackend

        backend = MockBackend(cfg, state)
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
    app = App(cfg, state, backend, display, touch)

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
    return 0


if __name__ == "__main__":
    sys.exit(main())
