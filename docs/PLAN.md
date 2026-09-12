# Build Plan

The goal: a Pi that boots straight into a full-screen 480x320 touch UI, rotated 180°, where
START and STOP control a wardriving capture and the screen shows networks and live status.

## Current state of `rustypi7` (checked 2026-09-12)

| Item | Found | Needs |
|------|-------|-------|
| OS | Debian 13 Trixie, arm64, kernel 6.18, `multi-user.target` (Lite, no desktop) | Nothing. Lite is the right base. |
| RAM / disk | 1.8 GiB / 109 GB free | Plenty |
| ALFA AWUS036ACM | `wlan1`, MT7612U, `monitor` mode supported | NetworkManager must stop managing it |
| Onboard Wi-Fi | `wlan0`, connected (SSH path) | Leave alone |
| GPS BU-353N | `/dev/ttyUSB0` (Prolific `067b:23a3`) | Install gpsd |
| Display | `HDMI-A-1` connected, defaulting to 1024x768 | Force 480x320, rotate |
| Touch | No input device; SPI disabled (`/dev/spidev*` absent) | `dtparam=spi=on` + ADS7846 overlay |
| Regulatory domain | `cfg80211.ieee80211_regdom=US` in cmdline | Done |
| Time | `systemd-timesyncd` (needs network) | Replace with chrony + GPS refclock |
| Packages | python3 3.13 only; no git, gpsd, kismet, pygame | apt install. Kismet comes from the Kismet repo. |
| User `russ` | In `video render input gpio spi dialout`; passwordless sudo | Good for DRM, evdev, and serial access |
| Power | `get_throttled=0x0` | Watch it in the car |

## Key decisions

| Decision | Choice | Why | Rejected |
|----------|--------|-----|----------|
| Capture engine | **Kismet** | Battle-tested monitor mode, hopping, GPS tagging, WiGLE CSV output, REST API for the UI | Custom scapy sniffer (reinventing the wheel, fragile); airodump-ng (poor API, CSV scraping) |
| GPS access | **gpsd**, shared by Kismet, chrony, and the UI | One owner of the serial port; the UI sees GPS even when capture is stopped | Kismet reading the serial port directly (locks everyone else out) |
| Clock | **chrony with gpsd SHM refclock** | No RTC and no network in the car, so log timestamps would be wrong otherwise | Ignoring it |
| UI toolkit | **Python + pygame (SDL2 KMS/DRM)** | No X or Wayland; fast boot; low RAM; easy custom widgets; apt package available | Kivy/Qt (heavy, awkward at 480x320); web UI in a kiosk browser (RAM, boot time, needs a compositor) |
| Touch input | **python-evdev + in-app 4-point calibration** | Handles axis swap, inversion, and the 180° flip in one place, without X calibration tools or udev matrix guesswork | SDL mouse events + libinput calibration matrix (hard to get right without a compositor) |
| Rotation | **App rotates its framebuffer 180°; kernel `rotate=180` covers the boot console** | SDL KMSDRM doesn't reliably honor connector rotation. Rotating a 480x320 surface costs nothing. | Relying on the DRM plane rotation property |
| Resolution handling | **Render a logical 480x320 canvas, then scale to the actual mode** | Keeps working if the panel rejects 480x320 and we have to fall back to 640x480 | Layout hardcoded to the output mode |
| Process model | **systemd units:** `wardrive-ui` (enabled, tty1) and `wardrive-kismet` (started on demand) | Auto-restart, journald logs, clean stop that flushes Kismet logs | UI spawning Kismet as a child (a UI crash kills the capture) |
| Privilege | Kismet suid capture helper + `kismet` group; narrow sudoers rule for `systemctl start/stop wardrive-kismet` and `shutdown` | The UI runs unprivileged | Running the UI as root |
| Where to develop | **This repo on the Ubuntu server, deployed over SSH** | Git and GitHub live here; the UI runs headless against a mock backend; the Pi stays reproducible via `install.sh` | Hand-editing on the Pi (drifts, not reproducible) |

---

## Phases

Each phase ends with a check that must pass on the real hardware before moving on.

### Phase 0: Repo and docs *(this step)*
- [x] README.md, docs/PLAN.md, .gitignore
- [ ] `git init` and create the GitHub repo (private)

### Phase 1: Base system and capture, from the command line
Proves the radio and GPS work before any UI is written.
- [ ] `scripts/install.sh`, first part: apt packages (`gpsd gpsd-clients chrony python3-pygame
      python3-evdev python3-requests evtest git rsync`)
- [ ] Add the Kismet apt repo for Trixie and install `kismet`. Choose suid helpers and add
      `russ` to the `kismet` group.
- [ ] gpsd: `/etc/default/gpsd`, with `DEVICES` set to the stable `/dev/serial/by-id/...` path
      and `GPSD_OPTIONS="-n"`
- [ ] chrony: `refclock SHM 0 refid GPS precision 1e-1 offset 0.2 delay 0.2` and
      `makestep 1 -1`. Disable timesyncd.
- [ ] NetworkManager: `unmanaged-devices=interface-name:wlan1` (better: match the ALFA's MAC
      `00:c0:ca:bd:04:e1`, since interface names can swap)
- [ ] `config/kismet_site.conf`:
  ```
  source=wlan1:name=alfa,type=linuxwifi
  gps=gpsd:host=localhost,port=2947,reconnect=true
  log_prefix=/home/russ/wardrive/logs
  log_types=kismet,wiglecsv
  httpd_bind_address=127.0.0.1
  ```
- [ ] Generate Kismet REST credentials or an API key on the Pi, stored outside the repo

**Check:** `cgps` shows a 3D fix. `chronyc sources` lists GPS. `kismet` run from SSH puts
`wlan1` in monitor mode, hops channels on both bands, lists APs, and writes a `.wiglecsv`
whose rows have coordinates.

### Phase 2: Display and touch bring-up
- [ ] `scripts/boot-config.sh` (idempotent, backs up to `*.bak-<date>`):
  - `config.txt [all]`: `dtparam=spi=on`, `dtoverlay=ads7846,penirq=25,speed=50000,...`
    (or OSOYOO's `bw-ads7846` overlay from their Trixie guide, if the stock one misbehaves)
  - `cmdline.txt` (single line, append): `video=HDMI-A-1:480x320M@60D,rotate=180
    vt.global_cursor_default=0 consoleblank=0 quiet loglevel=3 logo.nologo`
- [ ] Reboot, then check `cat /sys/class/drm/card1-HDMI-A-1/modes` and confirm the panel shows
      a sharp, full-screen image
- [ ] `sudo evtest` shows an `ADS7846 Touchscreen` device, and tapping produces
      `ABS_X`/`ABS_Y`/`BTN_TOUCH`. If nothing appears, try `cs=0` and `cs=1`.
- [ ] Record the raw min/max values at the four corners (these seed the default calibration)

**Check:** the boot console is right-side up with the HDMI adapter pointing down, and evtest
reports touches from all four corners.
**Fallback:** if the panel rejects 480x320, use `640x480M@60`; the UI scales.

### Phase 3: UI skeleton, off-Pi first
- [ ] `display.py`: `SDL_VIDEODRIVER=kmsdrm` on the Pi, windowed or `dummy` elsewhere; logical
      480x320 surface, then rotate 180°, then scale and blit; about 15 FPS cap to save CPU
- [ ] `touch.py`: background evdev reader thread that finds the device by name, turns raw
      coordinates into logical ones through the calibration (affine fit from 4 points, which
      handles swap and inversion), and emits tap and long-press events
- [ ] `calibrate.py`: crosshair screen, saves `~/.config/wardrive/touch.json`, runs on first boot
- [ ] `widgets.py`: button (with a pressed state), scrolling list (drag or page buttons),
      status bar, footer
- [ ] Views: NETS, STATS, GPS, LOG, MENU, all driven by a `mock.py` backend
- [ ] `--screenshot` flag renders one frame to PNG, so layouts can be reviewed on the headless
      server

**Check:** screenshots of every view look right at 480x320. On the Pi, running from SSH with
the getty stopped, the UI shows on the LCD and taps land where your finger is.

### Phase 4: Real data
- [ ] `gps.py`: gpsd JSON client (`?WATCH={"enable":true,"json":true}`) giving fix, sats,
      position, and speed, with auto-reconnect
- [ ] `capture.py`: `sudo systemctl start|stop wardrive-kismet`, watch `is-active`, track the
      session start time
- [ ] `kismet.py`: REST client (auth from a file on the Pi)
  - `/system/status.json`: uptime, memory, packet counts
  - `/datasource/all_sources.json`: source running, current channel, errors
  - `/devices/last-time/<ts>/devices.json`, filtered to `phydot11_accesspoints` with `fields`
    simplification (SSID, BSSID, channel, signal, crypto, first and last seen): incremental
    polling every 1–2 s, so the full device list is never re-fetched
  - Optional: `/eventbus/events.ws` for new-device and alert events feeding the LOG view
- [ ] Session summary on STOP: duration, total and new APs, log file paths

**Check:** a drive around the block shows networks appearing live and a GPS fix in the status
bar. STOP gives a closed `.wiglecsv` that uploads to WiGLE cleanly.

### Phase 5: Boot straight into the UI
- [ ] `system/wardrive-ui.service`:
  ```ini
  [Unit]
  After=systemd-user-sessions.service gpsd.service
  Conflicts=getty@tty1.service
  [Service]
  User=russ
  WorkingDirectory=/opt/wardrive
  Environment=SDL_VIDEODRIVER=kmsdrm PYTHONUNBUFFERED=1
  ExecStart=/usr/bin/python3 -m wardrive
  TTYPath=/dev/tty1
  StandardInput=tty
  Restart=always
  RestartSec=2
  [Install]
  WantedBy=multi-user.target
  ```
- [ ] `system/wardrive-kismet.service`: runs `kismet --no-ncurses` as `russ`,
      `KillSignal=SIGINT` and a generous `TimeoutStopSec` so logs are finalized
- [ ] `system/sudoers-wardrive`: NOPASSWD only for those exact `systemctl` and `shutdown`
      commands (validated with `visudo -cf`)
- [ ] Trim boot time: disable unneeded services (`cloud-init` after first boot, `bluetooth` if
      unused, `ModemManager`)
- [ ] `scripts/deploy.sh`: rsync to `/opt/wardrive`, run install, restart the UI

**Check:** from a cold power-on, the UI is on screen in under about 30 s with no console text
visible, and killing the UI process brings it back within seconds.

### Phase 6: Field hardening
- [ ] Failure states shown clearly in the UI: ALFA unplugged, GPS unplugged or no fix, Kismet
      crashed (offer a RESTART button), disk under 1 GB
- [ ] Hold-to-confirm for SHUTDOWN and REBOOT, to prevent pocket taps
- [ ] Under-voltage and throttling indicator from `vcgencmd get_throttled`
- [ ] Optional: start capture automatically on boot once GPS has a fix (config flag)
- [ ] Optional: read-only root with overlayfs and a separate writable log partition, so a hard
      power cut can't hurt the SD card
- [ ] `scripts/pull-logs.sh`, plus optional WiGLE API upload when back on home Wi-Fi
- [ ] Optional: log rotation or session cleanup, KML export

### Stretch ideas
- Bluetooth/BLE capture (Kismet `linuxbluetooth` source using the Pi's onboard BT)
- A physical GPIO button for start/stop that works without looking
- Speaker or buzzer chirp on new open networks
- Map view: breadcrumb trail plus AP dots drawn on the LCD, no tiles

---

## Risks and unknowns

| Risk | Impact | Mitigation |
|------|--------|------------|
| Panel's scaler rejects 480x320 | Blurry or no image | Fall back to 640x480; the UI scales from a logical canvas |
| Touch controller chip-select or IRQ pin differs from OSOYOO docs | No touch | Try `cs=0` and `cs=1` with evtest; use OSOYOO's `bw-ads7846` overlay |
| Screen's GPIO header not seated (it's powered externally) | No touch at all | Confirm the 12-pin header is on the Pi; touch needs SPI even when power comes from elsewhere |
| SDL KMSDRM can't get DRM master while getty or fbcon holds the VT | UI fails to start | `Conflicts=getty@tty1`, `TTYPath=/dev/tty1`; test early in Phase 3 |
| Kismet not packaged for Trixie in its repo | Install friction | Fall back to building from source (slow on a Pi 4, but one-time) |
| MT7612U drops out of monitor mode when a USB hub browns out | Capture stalls | Kismet source auto-reconnect; show source status in the UI; use a quality 3 A supply |
| Car power cuts cause SD corruption | Lost logs, unbootable Pi | Shutdown button now, overlayfs later |
| GPS cold start takes minutes | Early networks have no location | GPS view shows sats and fix; optional wait-for-fix auto-start |
| Interface names swap (`wlan0`/`wlan1`) | Kismet grabs the SSH radio | Pin the source by MAC address and have NetworkManager ignore the ALFA by MAC |

## Open questions

1. Is the screen's 12-pin header plugged onto the Pi's GPIO pins? It's needed for touch.
2. Should capture start automatically at boot, or always wait for a START tap? The plan's
   default is to wait for START.
3. WiGLE: upload by hand, or automatically when the Pi sees home Wi-Fi?
4. Capture Bluetooth too, or Wi-Fi only?
