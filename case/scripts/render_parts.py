#!/usr/bin/env python3
"""Render every STL in models/stl/ into one preview sheet (images/parts.png).

A small depth-buffered software rasterizer, so it needs only numpy and Pillow:

    pip install numpy pillow
    python scripts/render_parts.py
"""

import struct
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
# (file key, label, quantity to print, camera elevation, camera azimuth)
PARTS = [
    ("back", "Case back", 1, 38, -60),
    ("bezel", "Bezel", 1, 38, -60),
    ("knuckle", "Knuckle", 1, 28, -40),
    ("arm", "Arm", 1, 45, -70),
    ("tilt_plate", "Tilt plate", 2, 50, -60),
    ("base", "Base", 1, 30, -50),
    ("knob", "Knob", 2, 45, -60),
]
COLOR = np.array([0.18, 0.52, 0.64])
CELL, LABEL_H = 360, 46


def load_stl(path: Path) -> np.ndarray:
    """Binary STL -> (n, 3, 3) triangle vertices."""
    data = path.read_bytes()
    count = struct.unpack("<I", data[80:84])[0]
    record = np.dtype([("normal", "<f4", 3), ("v", "<f4", (3, 3)), ("attr", "<u2")])
    return np.frombuffer(data, dtype=record, count=count, offset=84)["v"].astype(np.float64)


def view_matrix(elev: float, azim: float) -> np.ndarray:
    a, e = np.radians(azim), np.radians(elev)
    forward = -np.array([np.cos(e) * np.cos(a), np.cos(e) * np.sin(a), np.sin(e)])
    right = np.cross(forward, [0.0, 0.0, 1.0])
    right /= np.linalg.norm(right)
    return np.stack([right, np.cross(right, forward), -forward])


def render(tris: np.ndarray, size: int, elev: float, azim: float, supersample: int = 2) -> Image.Image:
    s = size * supersample
    v = tris.reshape(-1, 3)
    v = (v - (v.min(0) + v.max(0)) / 2) @ view_matrix(elev, azim).T
    scale = 0.84 * s / max(np.ptp(v[:, 0]), np.ptp(v[:, 1]))
    screen = np.stack([v[:, 0] * scale + s / 2, s / 2 - v[:, 1] * scale], 1).reshape(-1, 3, 2)
    depth = (-v[:, 2]).reshape(-1, 3)  # smaller is closer to the camera

    faces = v.reshape(-1, 3, 3)
    normals = np.cross(faces[:, 1] - faces[:, 0], faces[:, 2] - faces[:, 0])
    normals /= np.linalg.norm(normals, axis=1, keepdims=True) + 1e-12
    light = np.array([-0.4, 0.55, 0.75])
    shade = 0.30 + 0.70 * np.abs(normals @ (light / np.linalg.norm(light)))

    zbuf = np.full((s, s), np.inf)
    img = np.ones((s, s, 3))
    for (p0, p1, p2), (z0, z1, z2), k in zip(screen, depth, shade):
        x0, x1 = int(max(min(p0[0], p1[0], p2[0]), 0)), int(min(max(p0[0], p1[0], p2[0]) + 1, s))
        y0, y1 = int(max(min(p0[1], p1[1], p2[1]), 0)), int(min(max(p0[1], p1[1], p2[1]) + 1, s))
        area = (p1[1] - p2[1]) * (p0[0] - p2[0]) + (p2[0] - p1[0]) * (p0[1] - p2[1])
        if x0 >= x1 or y0 >= y1 or abs(area) < 1e-9:
            continue
        gx, gy = np.meshgrid(np.arange(x0, x1) + 0.5, np.arange(y0, y1) + 0.5)
        w0 = ((p1[1] - p2[1]) * (gx - p2[0]) + (p2[0] - p1[0]) * (gy - p2[1])) / area
        w1 = ((p2[1] - p0[1]) * (gx - p2[0]) + (p0[0] - p2[0]) * (gy - p2[1])) / area
        w2 = 1 - w0 - w1
        inside = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
        if not inside.any():
            continue
        d = w0 * z0 + w1 * z1 + w2 * z2
        region = zbuf[y0:y1, x0:x1]
        closer = inside & (d < region)
        region[closer] = d[closer]
        img[y0:y1, x0:x1][closer] = COLOR * k + (1 - k) * 0.03
    return Image.fromarray((np.clip(img, 0, 1) * 255).astype(np.uint8)).resize((size, size), Image.LANCZOS)


def main() -> None:
    sheet = Image.new("RGB", (4 * CELL, 2 * (CELL + LABEL_H)), "white")
    draw = ImageDraw.Draw(sheet)
    try:
        font, small = ImageFont.truetype("DejaVuSans.ttf", 17), ImageFont.truetype("DejaVuSans.ttf", 14)
    except OSError:
        font = small = ImageFont.load_default()
    for i, (key, label, qty, elev, azim) in enumerate(PARTS):
        tris = load_stl(ROOT / "models" / "stl" / f"wardriving-case-{key}.stl")
        dims = np.ptp(tris.reshape(-1, 3), axis=0)
        x, y = (i % 4) * CELL, (i // 4) * (CELL + LABEL_H)
        sheet.paste(render(tris, CELL, elev, azim), (x, y + LABEL_H))
        draw.text((x + CELL / 2, y + 8), f"{label} ×{qty}" if qty > 1 else label, fill=(20, 20, 20), font=font, anchor="mt")
        draw.text((x + CELL / 2, y + 30), f"{dims[0]:.0f} × {dims[1]:.0f} × {dims[2]:.0f} mm", fill=(90, 90, 90), font=small, anchor="mt")
    for j, line in enumerate(["9 printed parts", "PLA, no supports", "M3 hardware only"]):
        draw.text((3 * CELL + 40, CELL + LABEL_H + 140 + j * 34), line, fill=(20, 20, 20), font=font)
    out = ROOT / "images" / "parts.png"
    sheet.save(out, optimize=True)
    print(out)


if __name__ == "__main__":
    main()
