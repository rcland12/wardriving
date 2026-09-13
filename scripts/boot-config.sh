#!/usr/bin/env bash
# Configure the Pi's boot files for the OSOYOO 3.5" HDMI touch display.
# Idempotent: re-running replaces the wardrive-managed block/params in place.
# Backups are written next to the originals as *.bak-wardrive-<timestamp>.
#
#   sudo ./scripts/boot-config.sh            # apply
#   sudo ./scripts/boot-config.sh --remove   # undo
#
# Env overrides:
#   WARDRIVE_HDMI_MODE   default unset         (let the panel's EDID pick; see below)
#   WARDRIVE_ROTATE      default 180           (text console only; the UI rotates itself)
#   WARDRIVE_QUIET       default 1             (hide boot messages; 0 to show them)
#   WARDRIVE_TOUCH_CS    default 1             (SPI chip select of the touch controller)
#
# HDMI mode: don't force one. The OSOYOO panel's EDID prefers 1280x720, which works,
# and its scaler shrinks it to the 480x320 glass. Lessons learned on this panel:
#   480x320M@60 -> "No Signal" (11.9 MHz pixel clock, below HDMI's 25 MHz minimum)
#   640x480@60  -> signal but black screen, despite being listed in the EDID
# If you must force one, use a mode from /sys/class/drm/card*-HDMI-A-1/modes.
#
# Rotation uses fbcon=rotate (software rotation of the text console) rather than
# video=...,rotate=180, which sets the DRM plane rotation property that then also
# applies to (and double-rotates) the UI.
set -euo pipefail

BOOT=/boot/firmware
CONFIG="$BOOT/config.txt"
CMDLINE="$BOOT/cmdline.txt"
MODE="${WARDRIVE_HDMI_MODE:-}"
ROTATE="${WARDRIVE_ROTATE:-180}"
QUIET="${WARDRIVE_QUIET:-1}"
TOUCH_CS="${WARDRIVE_TOUCH_CS:-1}"
case $ROTATE in
    0) FBCON_ROT=0 ;; 90) FBCON_ROT=1 ;; 180) FBCON_ROT=2 ;; 270) FBCON_ROT=3 ;;
    *) echo "WARDRIVE_ROTATE must be 0, 90, 180 or 270" >&2; exit 1 ;;
esac

BEGIN="# >>> wardrive (managed by scripts/boot-config.sh)"
END="# <<< wardrive"

[[ $EUID -eq 0 ]] || { echo "run as root (sudo)" >&2; exit 1; }
[[ -f $CONFIG && -f $CMDLINE ]] || { echo "boot files not found under $BOOT" >&2; exit 1; }

stamp=$(date +%Y%m%d-%H%M%S)
cp -a "$CONFIG" "$CONFIG.bak-wardrive-$stamp"
cp -a "$CMDLINE" "$CMDLINE.bak-wardrive-$stamp"

# --- config.txt: strip any previous block ------------------------------------
sed -i "/^# >>> wardrive/,/^# <<< wardrive/d" "$CONFIG"
sed -i -e :a -e '/^\n*$/{$d;N;ba' -e '}' "$CONFIG"   # drop trailing blank lines

# --- cmdline.txt: strip params we own (single line file) ---------------------
OWNED_RE='^(video=HDMI-A-1:.*|fbcon=rotate:.*|vt\.global_cursor_default=.*|consoleblank=.*|logo\.nologo|quiet|loglevel=.*)$'
# (cmdline.txt usually has no trailing newline, which makes read return 1)
read -r -a params < "$CMDLINE" || true
kept=()
for p in "${params[@]}"; do
    [[ $p =~ $OWNED_RE ]] || kept+=("$p")
done

if [[ ${1:-} == --remove ]]; then
    echo "${kept[*]}" > "$CMDLINE"
    echo "wardrive boot config removed (backups: *.bak-wardrive-$stamp). Reboot to apply."
    exit 0
fi

# The block goes at the very end, after the last [all], so it applies to every model.
{
    printf '\n%s\n' "$BEGIN"
    echo "[all]"
    echo "# Resistive touch (XPT2046/ADS7846) on SPI0, pen IRQ on GPIO25 (header pin 22)."
    echo "dtparam=spi=on"
    echo "dtoverlay=ads7846,cs=${TOUCH_CS},penirq=25,penirq_pull=2,speed=50000,keep_vref_on=0,swapxy=0,pmax=255,xohms=150,xmin=200,xmax=3900,ymin=200,ymax=3900"
    echo "disable_splash=1"
    echo "$END"
} >> "$CONFIG"

[[ -n $MODE ]] && kept+=("video=HDMI-A-1:${MODE}")
kept+=("fbcon=rotate:${FBCON_ROT}" "vt.global_cursor_default=0" "consoleblank=0")
(( QUIET )) && kept+=("logo.nologo" "quiet" "loglevel=3")
echo "${kept[*]}" > "$CMDLINE"

echo "config.txt block:"
sed -n "/^# >>> wardrive/,/^# <<< wardrive/p" "$CONFIG"
echo "cmdline.txt:"
cat "$CMDLINE"
echo "Backups: *.bak-wardrive-$stamp. Reboot to apply."
