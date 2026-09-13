"""python3 -m wardrive.demo on|off|status|regenerate [--config PATH]

Restart the UI afterwards (scripts/demo.sh does both).
"""

from __future__ import annotations

import argparse
import sys

from .. import config
from . import is_on, regenerate, status, turn_off, turn_on


def main() -> int:
    p = argparse.ArgumentParser(prog="wardrive.demo", description="Turn demo mode on or off")
    p.add_argument("command", choices=["on", "off", "status", "regenerate"])
    p.add_argument("--config", help="path to wardrive.toml")
    args = p.parse_args()
    cfg = config.load(args.config)

    def progress(i: int, n: int, name: str) -> None:
        print(f"  simulating drive {i}/{n}: {name}", flush=True)

    if args.command == "on":
        if is_on(cfg):
            print("demo mode is already on")
        else:
            turn_on(cfg, progress)
            print("demo mode on")
    elif args.command == "off":
        print("demo mode off; demo data deleted" if turn_off(cfg) else "demo mode was already off")
    elif args.command == "regenerate":
        regenerate(cfg, progress)
        print("demo sessions regenerated; demo mode on")
    print("\n".join(status(cfg)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
