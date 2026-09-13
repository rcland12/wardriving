#!/usr/bin/env bash
# Copy an offline map built by tools/build_map.py to the Pi and restart the UI.
#
#   ./scripts/push-map.sh georgia.map [host]      (default host: $WARDRIVE_HOST or rustypi7)
#
# The MAP screen opens the first *.map file in /var/lib/wardrive/maps.
set -euo pipefail

MAP="${1:?usage: push-map.sh FILE.map [host]}"
HOST="${2:-${WARDRIVE_HOST:-rustypi7}}"
[[ -f $MAP ]] || { echo "no such file: $MAP" >&2; exit 1; }

NAME="$(basename "$MAP")"
echo "==> copying $NAME ($(du -h "$MAP" | cut -f1)) to $HOST"
# Upload under a temporary name so the UI never opens a half-copied file.
rsync -ah --info=progress2 "$MAP" "$HOST:/var/lib/wardrive/maps/.$NAME.part"
ssh "$HOST" "mv /var/lib/wardrive/maps/.$NAME.part /var/lib/wardrive/maps/$NAME && sudo systemctl restart wardrive-ui"
echo "==> done; MAP screen restarted with $NAME"
