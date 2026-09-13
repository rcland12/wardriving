"""Display setup: a logical 480x320 canvas presented rotated/scaled to the output.

Backends:
  fbdev   Draw off-screen with pygame, then copy frames into /dev/fb0 (the kernel's
          framebuffer console device). Default on the Pi: on the OSOYOO HDMI panel
          SDL's KMS/DRM output stays black, while the framebuffer path displays.
  kmsdrm  SDL fullscreen straight to DRM/KMS (kept for other screens).
  window  Desktop window for development.
  headless SDL dummy driver, for screenshots and tests.
"""

from __future__ import annotations

import fcntl
import logging
import mmap
import os
from pathlib import Path

import pygame

log = logging.getLogger(__name__)

WIDTH, HEIGHT = 480, 320

KDSETMODE, KD_TEXT, KD_GRAPHICS = 0x4B3A, 0x00, 0x01


class FrameBuffer:
    """Memory-mapped /dev/fbN with its pixel format."""

    def __init__(self, device: str = "/dev/fb0"):
        sysfs = Path("/sys/class/graphics") / Path(device).name
        self.width, self.height = map(int, (sysfs / "virtual_size").read_text().strip().split(","))
        self.bpp = int((sysfs / "bits_per_pixel").read_text())
        self.stride = int((sysfs / "stride").read_text())
        if self.bpp == 16:
            self.masks = (0xF800, 0x07E0, 0x001F, 0)
        elif self.bpp == 32:
            self.masks = (0xFF0000, 0x00FF00, 0x0000FF, 0)
        else:
            raise RuntimeError(f"unsupported framebuffer depth {self.bpp} bpp")
        self._fd = os.open(device, os.O_RDWR)
        self._map = mmap.mmap(self._fd, self.stride * self.height, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE)
        log.info("framebuffer %s: %dx%d %d bpp", device, self.width, self.height, self.bpp)

    def surface(self, size: tuple[int, int]) -> pygame.Surface:
        """A surface in the framebuffer's native pixel format."""
        return pygame.Surface(size, depth=self.bpp, masks=self.masks)

    def write(self, frame: pygame.Surface) -> None:
        raw = frame.get_buffer().raw
        pitch = frame.get_pitch()
        if pitch == self.stride:
            self._map[: len(raw)] = raw
        else:
            row = self.width * self.bpp // 8
            for y in range(self.height):
                off = y * self.stride
                self._map[off : off + row] = raw[y * pitch : y * pitch + row]

    def close(self) -> None:
        self._map.close()
        os.close(self._fd)


def _set_console_mode(mode: int) -> None:
    """Stop (KD_GRAPHICS) or resume (KD_TEXT) the kernel console drawing on our VT."""
    for path in ("/dev/tty0", "/dev/tty1"):
        try:
            fd = os.open(path, os.O_RDWR | os.O_NOCTTY)
        except OSError:
            continue
        try:
            fcntl.ioctl(fd, KDSETMODE, mode)
            return
        except OSError as exc:
            log.debug("KDSETMODE on %s failed: %s", path, exc)
        finally:
            os.close(fd)
    if mode == KD_GRAPHICS:
        log.warning("could not switch the console to graphics mode; console text may overlay the UI")


class Display:
    def __init__(self, mode: str = "fbdev", rotate: int = 180, scale: int = 1, fb_device: str = "/dev/fb0"):
        """mode: "fbdev", "kmsdrm", "window" or "headless"."""
        self.mode = mode
        self.fb: FrameBuffer | None = None
        if mode in ("headless", "fbdev"):
            os.environ["SDL_VIDEODRIVER"] = "dummy"
        elif mode == "kmsdrm":
            os.environ.setdefault("SDL_VIDEODRIVER", "kmsdrm")
        pygame.display.init()
        pygame.font.init()

        self.rotate = rotate if mode in ("fbdev", "kmsdrm") else 0
        if mode == "fbdev":
            self.fb = FrameBuffer(fb_device)
            _set_console_mode(KD_GRAPHICS)
            pygame.display.set_mode((1, 1))  # dummy driver; needed for convert()/fonts
            # Draw directly in the framebuffer's pixel format so presenting is just
            # flip + scale + memcpy, with no per-frame format conversion.
            self.canvas = self.fb.surface((WIDTH, HEIGHT))
            self._out = self.fb.surface((self.fb.width, self.fb.height))
            self.screen_size = (self.fb.width, self.fb.height)
        elif mode == "kmsdrm":
            self.screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
            pygame.mouse.set_visible(False)
            self.canvas = pygame.Surface((WIDTH, HEIGHT))
            self.screen_size = self.screen.get_size()
        else:
            self.screen = pygame.display.set_mode((WIDTH * scale, HEIGHT * scale))
            pygame.display.set_caption("wardrive")
            self.canvas = pygame.Surface((WIDTH, HEIGHT))
            self.screen_size = self.screen.get_size()
        self.scale_factor = self.screen_size[0] / WIDTH

    def present(self) -> None:
        frame = self.canvas
        if self.rotate == 180:
            frame = pygame.transform.flip(frame, True, True)
        if self.fb is not None:
            if frame.get_size() == self.screen_size:
                self._out.blit(frame, (0, 0))
            else:
                # The HDMI panel is fed a larger mode (e.g. 1280x720) and downscales
                # it itself, so a cheap nearest-neighbour upscale is enough.
                pygame.transform.scale(frame, self.screen_size, self._out)
            self.fb.write(self._out)
            return
        if frame.get_size() != self.screen_size:
            frame = pygame.transform.scale(frame, self.screen_size)
        self.screen.blit(frame, (0, 0))
        pygame.display.flip()

    def close(self) -> None:
        if self.fb is not None:
            _set_console_mode(KD_TEXT)
            self.fb.close()
            self.fb = None

    def window_to_canvas(self, pos: tuple[int, int]) -> tuple[int, int]:
        """Mouse position in the dev window -> logical canvas coordinates."""
        return int(pos[0] / self.scale_factor), int(pos[1] / self.scale_factor)
