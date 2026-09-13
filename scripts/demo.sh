#!/usr/bin/env bash
# Demo mode on the Pi: fake sessions plus simulated GPS and capture, for trying the UI
# indoors. Real logs and upload records are untouched; "off" deletes only the demo data.
#
#   ./scripts/demo.sh on|off|status|regenerate
#
# Same as MENU -> DEMO on the touch screen. Run as your normal user (not with sudo).
set -euo pipefail

CMD="${1:-status}"
[[ $EUID -ne 0 ]] || { echo "run as your normal user, not root (demo files belong to the UI user)" >&2; exit 1; }

cd /opt/wardrive
python3 -m wardrive.demo "$CMD"
case $CMD in
    on|off|regenerate) sudo systemctl restart wardrive-ui && echo "UI restarted" ;;
esac
