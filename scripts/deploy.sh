#!/usr/bin/env bash
# Push this working tree to the Pi and run the installer there.
# Run FROM a dev machine with SSH access to the Pi:
#
#   ./scripts/deploy.sh [host] [--boot] [--reboot]
#
#   host      ssh destination (default: $WARDRIVE_HOST or rustypi7)
#   --boot    also apply scripts/boot-config.sh (display + touch boot settings)
#   --reboot  reboot the Pi afterwards
#   --ui-only just sync the UI code and restart the UI service (fast iteration)
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="${WARDRIVE_HOST:-rustypi7}"
BOOT=0 REBOOT=0 UI_ONLY=0
for arg in "$@"; do
    case $arg in
        --boot) BOOT=1 ;;
        --reboot) REBOOT=1 ;;
        --ui-only) UI_ONLY=1 ;;
        -*) echo "unknown option $arg" >&2; exit 2 ;;
        *) HOST=$arg ;;
    esac
done

STAGE=.cache/wardrive-src   # relative to the remote user's home
echo "==> syncing to $HOST:~/$STAGE"
rsync -az --delete --mkpath \
    --exclude .git --exclude logs/ --exclude screenshots/ --exclude '__pycache__' --exclude .pytest_cache \
    --exclude /notes/ --exclude /case/ --exclude wigle.txt --exclude '*.env' --exclude '*.map' --exclude '*.osm.pbf' \
    "$REPO/" "$HOST:$STAGE/"

if (( UI_ONLY )); then
    ssh "$HOST" "sudo rsync -a --delete --exclude __pycache__ ~/$STAGE/wardrive/ /opt/wardrive/wardrive/ && sudo systemctl restart wardrive-ui.service"
    echo "==> UI updated"
    exit 0
fi

echo "==> installing"
ssh -t "$HOST" "sudo bash ~/$STAGE/scripts/install.sh"
(( BOOT )) && ssh -t "$HOST" "sudo bash ~/$STAGE/scripts/boot-config.sh"
if (( REBOOT )); then
    echo "==> rebooting $HOST"
    ssh "$HOST" "sudo systemctl reboot" || true
fi
