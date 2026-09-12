# rustypi wardriver

A self-contained, touch-driven wardriving rig built on a Raspberry Pi 4B. Power it on and
it boots straight into a full-screen touch UI on a 3.5" LCD. Tap **START** to begin
capturing, watch networks scroll by with live GPS and capture stats, tap **STOP** to close
out the session. Logs are written in Kismet and WiGLE CSV formats.

> **Status:** planning / pre-alpha. See [`docs/PLAN.md`](docs/PLAN.md) for the build plan.

---

## Hardware

| Part | Model | Role | Linux view |
|------|-------|------|------------|
| SBC | Raspberry Pi 4 Model B (2 GB) | Host | Debian 13 "Trixie" (Raspberry Pi OS Lite, 64-bit) |
| Wi-Fi | ALFA AWUS036ACM (MediaTek MT7612U, dual-band AC1200) | Capture radio (monitor mode) | `wlan1`, in-kernel `mt76x2u` driver |
| GPS | GlobalSat BU-353N (USB) | Location + time source | `/dev/ttyUSB0` (Prolific PL2303 serial bridge) |
| Display | OSOYOO 3.5" HDMI LCD, 480x320, resistive touch | UI | HDMI (`HDMI-A-1`) + XPT2046/ADS7846 touch over SPI via GPIO header |

The onboard Wi-Fi (`wlan0`) stays in managed mode for SSH and log upload when you're home.
Only the ALFA is used for capture.

**Display orientation:** landscape, rotated 180° so the HDMI adapter points down.

**Touch note:** the screen gets its picture over HDMI, but the touch controller talks to the
Pi over SPI through the 12-pin GPIO header. Even if the screen has its own power supply,
that header must still be seated on the Pi for touch to work.

---

## How it works

```
               ┌──────────────────────────── Raspberry Pi 4B ─────────────────────────────┐
               │                                                                          │
 BU-353N ──USB─┼─► gpsd ──┬──────────────► chrony (sets system clock from GPS; no RTC)    │
               │          │                                                               │
               │          ├──────────────► Kismet ◄── wlan1 (ALFA, monitor mode, hopping) │
               │          │                  │  └─► logs: .kismet + .wiglecsv             │
               │          │                  │ REST API (127.0.0.1:2501)                  │
               │          ▼                  ▼                                            │
               │     ┌──────────── wardrive-ui (Python + pygame) ────────────┐            │
               │     │ status bar · network list · stats · GPS · menu        │──► HDMI ───┼─► 3.5" LCD
               │     │ start/stop Kismet via systemd · shutdown              │◄── SPI ────┼── touch
               │     └───────────────────────────────────────────────────────┘            │
               └──────────────────────────────────────────────────────────────────────────┘
```

- **Kismet** does the capture work: monitor mode, channel hopping across 2.4 and 5 GHz,
  802.11 parsing, GPS tagging, and log writing. It's the standard wardriving engine, and its
  WiGLE CSV output uploads directly to [wigle.net](https://wigle.net).
- **gpsd** owns the GPS. Kismet, chrony, and the UI all read from it, so you can see GPS
  status before capture starts.
- **chrony** sets the clock from GPS. The Pi has no real-time clock, so without network time
  the clock would be wrong and so would the log timestamps.
- **wardrive-ui** is a single Python/pygame program. It draws straight to the display through
  SDL's KMS/DRM backend, so there's no X11, Wayland, or desktop. It reads touch events from
  evdev and applies its own calibration, which handles the 180° flip. It controls Kismet
  through systemd and pulls live data from Kismet's REST API.
- **systemd** starts the UI on `tty1` at boot, in place of the login prompt, and restarts it
  if it crashes.

---

## The UI (480x320)

```
┌──────────────────────────────────────────────────────┐
│ ● REC 00:12:34    GPS 3D · 9 sat    14:02   52°C  ⚡ │  status bar
├───────────────────────────────────────┬──────────────┤
│ SSID               CH   dBm  SEC      │              │
│ ATT-5G-8812        149  -61  WPA2     │    STOP      │
│ <hidden>             6  -80  WPA3     │              │
│ xfinitywifi         11  -72  OPEN     ├──────────────┤
│ NETGEAR47           36  -77  WPA2     │  NETS  STATS │
│ ...                                   ├──────────────┤
│                                       │  GPS   MENU  │
├───────────────────────────────────────┴──────────────┤
│ APs 1,284 · new 312 · open 41 · 820 pkt/s · ch 36    │  footer
└──────────────────────────────────────────────────────┘
```

| View | Shows |
|------|-------|
| **NETS** | Live network list, newest or strongest first, color-coded by security. New networks flash briefly. |
| **STATS** | Session totals, security breakdown, packets/sec, channels, distance driven, log file size, free disk. |
| **GPS** | Fix type, satellites, lat/lon, altitude, speed, heading, HDOP, clock sync status. |
| **LOG** | Recent events: capture started, source in monitor mode, GPS fix gained or lost, errors. |
| **MENU** | Shutdown (hold), reboot, touch calibration, exit to console. |

The status bar also flags problems: no GPS fix, ALFA missing, Kismet down, low disk,
under-voltage or throttling.

Buttons are large (at least 60 px) because resistive touch is imprecise with fingers.

---

## Repository layout (planned)

```
wardriving/
├── README.md
├── hardware.txt                 # parts list with retail descriptions
├── docs/
│   └── PLAN.md                  # phased build plan, decisions, risks
├── wardrive/                    # Python package: the touch UI
│   ├── __main__.py              # `python3 -m wardrive`
│   ├── app.py                   # main loop, view switching
│   ├── display.py               # pygame/KMS init, 180° rotation, scaling
│   ├── touch.py                 # evdev reader + calibration mapping
│   ├── calibrate.py             # 4-corner touch calibration screen
│   ├── views/                   # nets, stats, gps, log, menu
│   ├── widgets.py               # buttons, lists, status bar
│   ├── kismet.py                # Kismet REST client
│   ├── gps.py                   # gpsd JSON client
│   ├── capture.py               # start/stop via systemd, session tracking
│   ├── sysinfo.py               # temp, throttling, disk
│   ├── mock.py                  # fake Kismet + GPS for off-Pi development
│   └── config.py
├── config/
│   ├── wardrive.toml.example    # UI settings (rotation, sort order, etc.)
│   └── kismet_site.conf         # Kismet overrides (source, GPS, logging)
├── system/                      # files installed onto the Pi
│   ├── wardrive-ui.service
│   ├── wardrive-kismet.service
│   ├── gpsd.default
│   ├── chrony-gps.conf
│   ├── nm-unmanaged-wlan1.conf
│   └── sudoers-wardrive
├── scripts/
│   ├── install.sh               # run ON the Pi: packages, configs, services
│   ├── boot-config.sh           # idempotent config.txt / cmdline.txt edits (with backups)
│   ├── deploy.sh                # run FROM dev machine: rsync to Pi + install
│   └── pull-logs.sh             # copy capture logs off the Pi
└── tests/
```

---

## Setup (target workflow)

### 1. Flash the Pi

Raspberry Pi OS Lite (64-bit, Trixie). In Raspberry Pi Imager, set the hostname, user,
Wi-Fi, SSH key, and Wi-Fi country (`US`). *(Already done for `rustypi7`.)*

### 2. Deploy from a dev machine

```bash
git clone git@github.com:<you>/wardriving.git
cd wardriving
./scripts/deploy.sh rustypi7        # rsync repo to the Pi, run install.sh, reboot
```

`install.sh` does the following:

1. Installs `gpsd`, `gpsd-clients`, `chrony`, `python3-pygame`, `python3-evdev`,
   `python3-requests`, and `evtest`, plus Kismet from the official Kismet apt repo
   (Debian Trixie doesn't ship it).
2. Points gpsd at the GPS and adds it to chrony as a time source.
3. Stops NetworkManager from managing `wlan1` so Kismet can take it.
4. Writes Kismet site config: source `wlan1`, GPS via gpsd, `kismet` + `wiglecsv` logs
   under `~/wardrive/logs`, REST API bound to localhost only.
5. Edits boot config (backups are kept):
   - `config.txt`: `dtparam=spi=on` and the ADS7846 touch overlay
   - `cmdline.txt`: force `480x320` HDMI mode, rotate the console 180°, hide the cursor,
     disable console blanking, quiet boot
6. Installs and enables `wardrive-ui.service` on `tty1`. Also installs
   `wardrive-kismet.service` without enabling it, so capture starts from the UI.
7. Adds a limited sudoers rule so the UI can start and stop capture and shut down the Pi.

### 3. First boot

The UI starts on the LCD. On first run it opens the **touch calibration** screen: tap the
four crosshairs. Calibration is saved to `~/.config/wardrive/touch.json`.

### 4. Drive

1. Wait for **GPS 3D** in the status bar. Capture works without a fix, but those networks
   are logged without location.
2. Tap **START**.
3. Drive.
4. Tap **STOP**. Kismet closes its logs cleanly.
5. **MENU → hold SHUTDOWN** before you cut power. Pulling power while it's running can
   corrupt the SD card.

### 5. Get your data

```bash
./scripts/pull-logs.sh rustypi7     # copies ~/wardrive/logs/* to ./logs/ (gitignored)
```

Upload the `.wiglecsv` files at [wigle.net/uploads](https://wigle.net/uploads). Open
`.kismet` files with Kismet tools, e.g. `kismetdb_to_kml`.

---

## Development

The code lives in this repo and is developed on a normal Linux box, then deployed to the Pi.
The UI runs off-Pi against a mock backend:

```bash
source ~/.global_venv/bin/activate
pip install pygame evdev requests
WARDRIVE_MOCK=1 python -m wardrive                         # windowed, on a desktop
WARDRIVE_MOCK=1 SDL_VIDEODRIVER=dummy python -m wardrive --screenshot out.png   # headless
```

On the Pi, the UI uses the system Python packages from apt, so there's no venv and no ARM
wheel builds.

Useful Pi-side commands:

```bash
journalctl -u wardrive-ui -f           # UI logs
journalctl -u wardrive-kismet -f       # capture logs
gpsmon / cgps                          # GPS sanity check
sudo evtest                            # raw touch events
sudo systemctl stop wardrive-ui        # free the screen for debugging
```

---

## Legal and privacy

- Passive collection of broadcast beacons is generally legal in the US. Don't connect to,
  deauth, or attack networks you don't own. Check your local laws.
- Capture logs contain precise location history and nearby device identifiers. `logs/` is
  gitignored. Keep it that way, even in a private repo.
- Kismet's REST credentials are generated on the Pi and never committed.
