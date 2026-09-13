#!/usr/bin/env bash
# Provision a Raspberry Pi (Raspberry Pi OS Lite / Debian Trixie) as the wardriver.
# Run ON the Pi from any checkout of this repo:
#
#   sudo ./scripts/install.sh
#
# Idempotent. Does not touch boot files (see scripts/boot-config.sh) and does not
# reboot. The user that invoked sudo becomes the wardrive user.
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "run as root (sudo)" >&2; exit 1; }

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WD_USER="${WARDRIVE_USER:-${SUDO_USER:-}}"
[[ -n $WD_USER && $WD_USER != root ]] || { echo "set WARDRIVE_USER or run via sudo as a normal user" >&2; exit 1; }
WD_HOME="$(getent passwd "$WD_USER" | cut -d: -f6)"
INSTALL_DIR=/opt/wardrive
DATA_DIR=/var/lib/wardrive
ETC_DIR=/etc/wardrive

log() { printf '\n==> %s\n' "$*"; }

# Render a template (@USER@, @HOME@) to a destination with a given mode.
render() { # src dst mode
    sed -e "s|@USER@|$WD_USER|g" -e "s|@HOME@|$WD_HOME|g" "$1" > "$2.tmp"
    chmod "$3" "$2.tmp"
    mv "$2.tmp" "$2"
}

# --- Packages -----------------------------------------------------------------
log "Kismet apt repository"
KEYRING=/usr/share/keyrings/kismet-archive-keyring.gpg
CODENAME="$(. /etc/os-release && echo "$VERSION_CODENAME")"
if [[ ! -f $KEYRING ]]; then
    curl -fsSL https://www.kismetwireless.net/repos/kismet-release.gpg.key | gpg --dearmor > "$KEYRING"
fi
echo "deb [signed-by=$KEYRING] https://www.kismetwireless.net/repos/apt/release/$CODENAME $CODENAME main" \
    > /etc/apt/sources.list.d/kismet.list

log "apt packages"
echo "kismet-common kismet-common/install-setuid boolean true" | debconf-set-selections
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
    gpsd gpsd-clients chrony fake-hwclock \
    python3 python3-pygame python3-evdev python3-requests \
    libegl1 libegl-mesa0 libgles2 libgl1-mesa-dri libgbm1 fonts-dejavu-core libdrm-tests \
    evtest git rsync curl libcap2-bin \
    kismet-core kismet-capture-linux-wifi kismet-capture-linux-bluetooth kismet-logtools

usermod -aG kismet,video,render,input,dialout,spi,gpio "$WD_USER"

# --- Capture adapter naming + NetworkManager ----------------------------------
log "capture adapter -> alfa0, unmanaged by NetworkManager"
install -m 0644 "$REPO/system/10-wardrive-alfa.link" /etc/systemd/network/10-wardrive-alfa.link
install -m 0644 "$REPO/system/nm-wardrive.conf" /etc/NetworkManager/conf.d/99-wardrive.conf
# Links only apply when the device is (re)added; initramfs may carry .link files too.
command -v update-initramfs >/dev/null && update-initramfs -u >/dev/null 2>&1 || true
systemctl reload NetworkManager 2>/dev/null || true

# --- GPS ----------------------------------------------------------------------
log "gpsd"
GPS_DEV="$(ls /dev/serial/by-id/* 2>/dev/null | head -n1 || true)"
GPS_DEV="${WARDRIVE_GPS_DEVICE:-${GPS_DEV:-/dev/ttyUSB0}}"
cat > /etc/default/gpsd <<EOF
# Managed by wardriving/scripts/install.sh
DEVICES="$GPS_DEV"
# -n: poll the GPS even with no clients, so a fix is ready before capture starts.
GPSD_OPTIONS="-n"
USBAUTO="true"
EOF
echo "GPS device: $GPS_DEV"
systemctl enable --now gpsd.socket gpsd.service
systemctl restart gpsd.service

log "chrony GPS time source"
install -m 0644 "$REPO/system/chrony-gps.conf" /etc/chrony/conf.d/wardrive-gps.conf
systemctl disable --now systemd-timesyncd.service 2>/dev/null || true
systemctl enable chrony.service
systemctl restart chrony.service

# No RTC: fake-hwclock restores the last saved time at boot and saves it at shutdown,
# so the clock starts near "last time the Pi was on" instead of whenever
# systemd-timesyncd last touched its clock file. The UI still waits for a real sync
# (GPS or NTP) before capturing. (fake-hwclock >= 0.14 ships split units; the old
# fake-hwclock.service is masked by the package on purpose.)
install -d -m 0755 /etc/systemd/system/fake-hwclock-save.timer.d
cat > /etc/systemd/system/fake-hwclock-save.timer.d/wardrive.conf <<'EOF'
# Car power is often cut without a shutdown; save every 10 minutes, not hourly.
[Timer]
OnCalendar=
OnCalendar=*:0/10
EOF
systemctl daemon-reload
systemctl enable fake-hwclock-load.service fake-hwclock-save.service fake-hwclock-save.timer
systemctl restart fake-hwclock-save.timer
fake-hwclock save

log "persistent journal"
install -d -m 0755 /etc/systemd/journald.conf.d
install -m 0644 "$REPO/system/journald-wardrive.conf" /etc/systemd/journald.conf.d/90-wardrive.conf
install -d -m 2755 -g systemd-journal /var/log/journal
systemd-tmpfiles --create --prefix /var/log/journal
systemctl restart systemd-journald
journalctl --flush

# --- Kismet -------------------------------------------------------------------
log "Kismet config"
install -m 0644 "$REPO/config/kismet_site.conf" /etc/kismet/kismet_site.conf
install -d -o "$WD_USER" -g "$WD_USER" -m 0750 "$DATA_DIR" "$DATA_DIR/logs"

# REST API credentials: generated once, readable only by the wardrive user.
KIS_DIR="$WD_HOME/.kismet"
KIS_AUTH="$KIS_DIR/kismet_httpd.conf"
install -d -o "$WD_USER" -g "$WD_USER" -m 0700 "$KIS_DIR"
if ! grep -q '^httpd_password=' "$KIS_AUTH" 2>/dev/null; then
    umask 077
    printf 'httpd_username=wardrive\nhttpd_password=%s\n' "$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9')" > "$KIS_AUTH"
    chown "$WD_USER:$WD_USER" "$KIS_AUTH"
    echo "generated Kismet REST credentials in $KIS_AUTH"
fi

render "$REPO/system/wardrive-kismet.service" /etc/systemd/system/wardrive-kismet.service 0644

# Onboard Bluetooth is soft-blocked by default; systemd-rfkill persists the unblock.
rfkill unblock bluetooth 2>/dev/null || true

# --- Privileges for the UI ----------------------------------------------------
log "sudoers rule"
render "$REPO/system/sudoers-wardrive" /etc/sudoers.d/wardrive.tmp 0440
visudo -cf /etc/sudoers.d/wardrive.tmp >/dev/null
mv /etc/sudoers.d/wardrive.tmp /etc/sudoers.d/wardrive

# --- Touch UI -----------------------------------------------------------------
if [[ -f $REPO/wardrive/__main__.py ]]; then
    log "touch UI -> $INSTALL_DIR"
    install -d -m 0755 "$INSTALL_DIR"
    rsync -a --delete --exclude '__pycache__' "$REPO/wardrive/" "$INSTALL_DIR/wardrive/"
    install -d -m 0755 "$ETC_DIR"
    # Holds upload credentials: readable by root and the wardrive user only.
    [[ -f $ETC_DIR/wardrive.toml ]] || install -m 0640 -g "$WD_USER" "$REPO/config/wardrive.toml.example" "$ETC_DIR/wardrive.toml"
    render "$REPO/system/wardrive-ui.service" /etc/systemd/system/wardrive-ui.service 0644
    systemctl daemon-reload
    systemctl enable wardrive-ui.service
    systemctl restart wardrive-ui.service || true
else
    systemctl daemon-reload
    echo "(touch UI not present in this checkout yet; skipping)"
fi

log "done"
echo "If boot files haven't been configured yet: sudo ./scripts/boot-config.sh && sudo reboot"
