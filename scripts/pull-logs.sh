#!/usr/bin/env bash
# Copy capture logs off the Pi. Never deletes anything on either side.
#
#   ./scripts/pull-logs.sh [host] [dest]     (defaults: rustypi7, ./logs)
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="${1:-${WARDRIVE_HOST:-rustypi7}}"
DEST="${2:-$REPO/logs}"

mkdir -p "$DEST"
# Skip sessions still being written (Kismet keeps a -journal next to open dbs).
rsync -av --exclude '*.kismet-journal' "$HOST:/var/lib/wardrive/logs/" "$DEST/"
echo "logs in $DEST"
