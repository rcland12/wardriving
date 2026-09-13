"""Colors, fonts and cached text rendering. Dark palette for night driving."""

from __future__ import annotations

import pygame

BG = (11, 15, 20)
PANEL = (22, 28, 36)
PANEL_HI = (36, 45, 57)
BORDER = (48, 58, 72)
TEXT = (230, 237, 243)
DIM = (125, 133, 144)
GREEN = (63, 185, 80)
RED = (248, 81, 73)
AMBER = (210, 153, 34)
BLUE = (88, 166, 255)
PURPLE = (188, 140, 255)
DARK_RED = (110, 28, 28)
DARK_GREEN = (22, 84, 38)
AMBER_DARK = (92, 66, 12)

# Network security colors. Chosen to stay distinct with red-green (deutan, protan) and
# blue-yellow (tritan) color vision deficiency, checked by simulation, and never used alone:
# the map also gives each a shape (maprender.MARKER_SHAPES) and lists print the label.
SEC_OPEN = (255, 221, 0)  # yellow
SEC_WEAK = (220, 90, 30)  # orange: WEP and WPA
SEC_WPA2 = (40, 120, 230)  # blue
SEC_WPA3 = (235, 235, 235)  # white
SEC_BT = (230, 110, 190)  # pink

CRYPT_COLORS = {
    "OPEN": SEC_OPEN,
    "WEP": SEC_WEAK,
    "WPA": SEC_WEAK,
    "WPA2": SEC_WPA2,
    "WPA3": SEC_WPA3,
    "BT": SEC_BT,
}

# Signal strength by brightness rather than hue: strong, medium, weak.
SIGNAL_STRONG, SIGNAL_MEDIUM, SIGNAL_WEAK = TEXT, (170, 177, 187), (105, 112, 122)

LEVEL_COLORS = {"info": TEXT, "good": GREEN, "warn": AMBER, "error": RED}

_fonts: dict[tuple[int, bool, bool], pygame.font.Font] = {}
_text_cache: dict[tuple, pygame.Surface] = {}


def font(size: int, bold: bool = False, mono: bool = False) -> pygame.font.Font:
    key = (size, bold, mono)
    if key not in _fonts:
        name = "dejavusansmono" if mono else "dejavusans"
        path = pygame.font.match_font(name, bold=bold)
        _fonts[key] = pygame.font.Font(path, size)  # path None -> pygame default font
    return _fonts[key]


def text(s: str, size: int, color=TEXT, bold: bool = False, mono: bool = False) -> pygame.Surface:
    key = (s, size, color, bold, mono)
    surf = _text_cache.get(key)
    if surf is None:
        if len(_text_cache) > 1024:
            _text_cache.clear()
        surf = font(size, bold, mono).render(s, True, color)
        _text_cache[key] = surf
    return surf


def fit(s: str, size: int, max_width: int, bold: bool = False, mono: bool = False) -> str:
    """Truncate s with an ellipsis so it renders within max_width pixels."""
    f = font(size, bold, mono)
    if f.size(s)[0] <= max_width:
        return s
    while s and f.size(s + "…")[0] > max_width:
        s = s[:-1]
    return s + "…"


def blit_text(surface, s, pos, size, color=TEXT, bold=False, mono=False, anchor="topleft"):
    surf = text(s, size, color, bold, mono)
    rect = surf.get_rect(**{anchor: pos})
    surface.blit(surf, rect)
    return rect
