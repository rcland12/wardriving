#!/usr/bin/env bash
# Visual display diagnostics. Watch the LCD while this runs; each phase says in the
# terminal what should be on screen. Note which phases actually show a picture.
#
#   sudo ./scripts/display-test.sh
#
# Phases isolate the display stack layer by layer:
#   A  libdrm test pattern (opaque XRGB buffer, no SDL)   -> HDMI mode + panel OK?
#   B  kernel text console (fbcon)                        -> console rotation?
#   C  pygame display surface via SDL KMSDRM (app's path)  -> SDL/pygame path OK?
#   D  pygame SDL Renderer, opaque clear                   -> alternative SDL path OK?
#   E  pygame rendered off-screen, copied to /dev/fb0      -> fbdev path OK?
# The wardrive UI is stopped during the test and restarted afterwards.
set -uo pipefail
[[ $EUID -eq 0 ]] || { echo "run as root (sudo)" >&2; exit 1; }

SECS="${SECS:-8}"
PHASES="${PHASES:-A B C D E}"   # e.g. PHASES="C D E"
want() { [[ " $PHASES " == *" $1 "* ]]; }
was_active=$(systemctl is-active wardrive-ui.service || true)
restore() {
    systemctl stop getty@tty1.service 2>/dev/null
    [[ $was_active == active ]] && systemctl start wardrive-ui.service
    echo; echo "done: UI restored ($was_active)"
}
trap restore EXIT

phase() {
    printf '\n\033[1;33m[%s]\033[0m %s (%ss)\n' "$1" "$2" "$SECS"
    sleep 1
}

systemctl stop wardrive-ui.service
sleep 1

conn=$(modetest -M vc4 -c 2>/dev/null | awk '$3=="connected" && $4 ~ /HDMI/ {print $1; exit}')
mode=$(awk -F: '{print $1}' /sys/class/drm/card*-HDMI-A-1/modes 2>/dev/null | head -1)
cur=$(grep -o 'video=HDMI-A-1:[^ ]*' /proc/cmdline | sed 's/video=HDMI-A-1://; s/[@,].*//')
mode=${cur:-$mode}
echo "connector=$conn mode=$mode"

if want A; then
    phase A "Colored test pattern (stripes/boxes) should fill the screen"
    timeout "$SECS" modetest -M vc4 -s "${conn}:${mode}" >/dev/null 2>&1
fi

if want B; then
    phase B "Text console with a login prompt (small white text)"
    systemctl start getty@tty1.service
    sleep "$SECS"
    systemctl stop getty@tty1.service
fi

py_code='
import os, sys, time
phase, secs = sys.argv[1], float(sys.argv[2])
if phase != "E":
    os.environ["SDL_VIDEODRIVER"] = "kmsdrm"
else:
    os.environ["SDL_VIDEODRIVER"] = "dummy"
import pygame
pygame.display.init(); pygame.font.init()
big = pygame.font.Font(None, 260)

def frame(size, color):
    s = pygame.Surface(size)
    s.fill(color)
    r = big.render(phase, True, (255, 255, 255))
    s.blit(r, r.get_rect(center=(size[0] // 2, size[1] // 2)))
    s.blit(pygame.font.Font(None, 40).render("TOP", True, (255, 255, 0)), (10, 10))
    return s

t0 = time.time()
if phase == "C":
    screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
    img = frame(screen.get_size(), (200, 0, 0))
    while time.time() - t0 < secs:
        screen.blit(img, (0, 0)); pygame.display.flip(); time.sleep(0.05)
elif phase == "D":
    from pygame._sdl2.video import Window, Renderer, Texture
    w, h = pygame.display.get_desktop_sizes()[0]
    win = Window("test", size=(w, h), fullscreen=True)
    ren = Renderer(win)
    tex = Texture.from_surface(ren, frame((w, h), (0, 150, 0)))
    while time.time() - t0 < secs:
        ren.draw_color = (0, 150, 0, 255); ren.clear(); tex.draw(); ren.present(); time.sleep(0.05)
elif phase == "E":
    fb = "/sys/class/graphics/fb0"
    w, h = map(int, open(fb + "/virtual_size").read().strip().split(","))
    bpp = int(open(fb + "/bits_per_pixel").read())
    masks = (0xF800, 0x07E0, 0x001F, 0) if bpp == 16 else (0xFF0000, 0xFF00, 0xFF, 0)
    native = pygame.Surface((w, h), depth=bpp, masks=masks)
    native.blit(frame((w, h), (0, 0, 200)), (0, 0))
    buf, pitch, row = native.get_buffer().raw, native.get_pitch(), w * bpp // 8
    data = b"".join(buf[y * pitch:y * pitch + row] for y in range(h))
    with open("/dev/fb0", "r+b") as f:
        while time.time() - t0 < secs:
            f.seek(0); f.write(data); f.flush(); time.sleep(0.2)
'

if want C; then
    phase C "RED screen with a big white C and yellow TOP label (SDL display surface)"
    python3 -c "$py_code" C "$SECS" 2>&1 | grep -v "Hello from\|^pygame "
fi

if want D; then
    phase D "GREEN screen with a big white D and yellow TOP label (SDL renderer path)"
    python3 -c "$py_code" D "$SECS" 2>&1 | grep -v "Hello from\|^pygame "
fi

if want E; then
    phase E "BLUE screen with a big white E and yellow TOP label (framebuffer path)"
    printf '\033[?25l' > /dev/tty1
    python3 -c "$py_code" E "$SECS" 2>&1 | grep -v "Hello from\|^pygame "
fi

echo
echo "Which phases (A-E) showed a picture, and was the TOP label at the top?"
