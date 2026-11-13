"""Render the app icon with the standard library only (no Pillow needed).

Writes:
  src/assets/icon.png                   1024x1024 source icon used by `flet build` for every platform
  src/assets/icon.ico                   multi-size window/taskbar icon for Windows source runs
  packaging/windows/budget_tracker.ico  multi-size .ico for the Windows shortcut launcher
  packaging/linux/budget_tracker.png    256x256 icon for the Linux/macOS source launchers

Run from the project root:  python packaging/make_icons.py
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

INDIGO = (57, 73, 171)
INDIGO_DARK = (40, 53, 147)
LAVENDER = (159, 168, 218)
MINT = (102, 187, 106)
WHITE = (255, 255, 255)

# (center_x, center_y, half_w, half_h, radius, color) in unit coordinates, drawn back to front.
SHAPES = [
    (0.50, 0.50, 0.50, 0.50, 0.22, INDIGO),  # tile
    (0.46, 0.34, 0.25, 0.08, 0.04, MINT),  # bill peeking out of the wallet
    (0.50, 0.56, 0.31, 0.22, 0.07, WHITE),  # wallet body
    (0.73, 0.56, 0.12, 0.08, 0.05, LAVENDER),  # clasp
]
CLASP_DOT = (0.70, 0.56, 0.028, INDIGO_DARK)


def _rounded_rect_sdf(px: float, py: float, cx: float, cy: float, hw: float, hh: float, r: float) -> float:
    qx = abs(px - cx) - (hw - r)
    qy = abs(py - cy) - (hh - r)
    outside = math.hypot(max(qx, 0.0), max(qy, 0.0))
    return outside + min(max(qx, qy), 0.0) - r


def render(size: int) -> bytes:
    """Return raw RGBA scanlines (each prefixed with a PNG filter byte) for a size x size icon."""
    out = bytearray()
    aa = 1.0 / size  # one pixel of anti-aliasing, in unit coordinates
    dx, dy, dr, dcol = CLASP_DOT
    for y in range(size):
        out.append(0)  # PNG filter type "None"
        py = (y + 0.5) / size
        for x in range(size):
            px = (x + 0.5) / size
            layers = [(_rounded_rect_sdf(px, py, cx, cy, hw, hh, rad), col) for cx, cy, hw, hh, rad, col in SHAPES]
            layers.append((math.hypot(px - dx, py - dy) - dr, dcol))
            r = g = b = a = 0.0
            for dist, col in layers:
                cover = min(max(0.5 - dist / aa, 0.0), 1.0)
                if cover:  # "over" compositing with premultiplied colour
                    r = col[0] * cover + r * (1 - cover)
                    g = col[1] * cover + g * (1 - cover)
                    b = col[2] * cover + b * (1 - cover)
                    a = cover + a * (1 - cover)
            out += bytes((round(r / a), round(g / a), round(b / a), round(a * 255))) if a else b"\0\0\0\0"
    return bytes(out)


def png(size: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8-bit RGBA
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(render(size), 9)) + chunk(b"IEND", b"")


def ico(sizes: list[int]) -> bytes:
    """Windows .ico with PNG-compressed entries (supported since Windows Vista)."""
    images = [png(s) for s in sizes]
    offset = 6 + 16 * len(sizes)
    entries = b""
    for s, data in zip(sizes, images):
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    return struct.pack("<HHH", 0, 1, len(sizes)) + entries + b"".join(images)


def main() -> None:
    targets = {
        ROOT / "src" / "assets" / "icon.png": lambda: png(1024),
        ROOT / "src" / "assets" / "icon.ico": lambda: ico([16, 24, 32, 48, 64, 256]),
        ROOT / "packaging" / "windows" / "budget_tracker.ico": lambda: ico([16, 24, 32, 48, 64, 256]),
        ROOT / "packaging" / "linux" / "budget_tracker.png": lambda: png(256),
    }
    for path, make in targets.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(make())
        print(f"wrote {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
