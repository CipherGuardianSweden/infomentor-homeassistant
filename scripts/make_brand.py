#!/usr/bin/env python3
"""Genererar brand/icon.png och brand/logo.png (enda stdlib, ingen PIL).

Motivet: en rundad kvadrat med blå-lila gradient och en vit studentmössa.
3x supersampling ger mjuka kanter.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "custom_components" / "infomentor" / "brand"
SS = 3  # supersampling

BLUE = (10, 132, 255)
INDIGO = (94, 92, 230)
WHITE = (255, 255, 255)


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def blend(c1: tuple[int, int, int], c2: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(round(lerp(c1[i], c2[i], t)) for i in range(3))  # type: ignore[return-value]


def point_in_poly(x: float, y: float, poly: list[tuple[float, float]]) -> bool:
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def rounded_rect(x: float, y: float, size: float, radius: float) -> bool:
    cx = min(max(x, radius), size - radius)
    cy = min(max(y, radius), size - radius)
    return (x - cx) ** 2 + (y - cy) ** 2 <= radius**2


def cap_polygons(size: float) -> list[list[tuple[float, float]]]:
    """Studentmössa: platt mortarboard, krona och en liten tofs."""
    cx, cy = 0.5 * size, 0.38 * size
    hw, hh = 0.30 * size, 0.095 * size
    diamond = [(cx, cy - hh), (cx + hw, cy), (cx, cy + hh), (cx - hw, cy)]
    base = [
        (0.36 * size, 0.44 * size),
        (0.64 * size, 0.44 * size),
        (0.69 * size, 0.64 * size),
        (0.31 * size, 0.64 * size),
    ]
    # tofs: ett tunt snöre ut från mössans högra hörn + en liten kula
    cord = [(0.78 * size, 0.30 * size), (0.81 * size, 0.30 * size), (0.84 * size, 0.55 * size), (0.81 * size, 0.55 * size)]
    return [diamond, base, cord]


def render(size: int, height: int | None = None, square: bool = True) -> list[list[tuple[int, int, int, int]]]:
    height = height or size
    caps = cap_polygons(size if square else height)
    rows: list[list[tuple[int, int, int, int]]] = []
    for py in range(height):
        row: list[tuple[int, int, int, int]] = []
        for px in range(size):
            r = g = b = a = 0
            for sy in range(SS):
                for sx in range(SS):
                    x = px + (sx + 0.5) / SS
                    y = py + (sy + 0.5) / SS
                    if not square:
                        # logga: centrera kvadraten i en bredare canvas
                        ox = (size - height) / 2
                        if x < ox or x >= ox + height:
                            continue
                        lx, ly = x - ox, y
                    else:
                        lx, ly = x, y
                    if not rounded_rect(lx, ly, height, height * 0.22):
                        continue
                    bg = blend(BLUE, INDIGO, (lx + ly) / (2 * height))
                    colour = bg
                    if any(point_in_poly(lx, ly, poly) for poly in caps):
                        colour = WHITE
                    elif (lx - 0.805 * height) ** 2 + (ly - 0.575 * height) ** 2 <= (0.035 * height) ** 2:
                        colour = WHITE
                    r += colour[0]
                    g += colour[1]
                    b += colour[2]
                    a += 255
            samples = SS * SS
            if a:
                # a = 255 * antal_täckta; medelfärg = summa / antal = summa * 255 / a
                row.append((r * 255 // a, g * 255 // a, b * 255 // a, a // samples))
            else:
                row.append((0, 0, 0, 0))
        rows.append(row)
    return rows


def write_png(path: Path, rows: list[list[tuple[int, int, int, int]]]) -> None:
    height = len(rows)
    width = len(rows[0])
    raw = b"".join(b"\x00" + b"".join(struct.pack("4B", *px) for px in row) for row in rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(png)
    print(f"skrev {path} ({width}×{height}, {len(png)} B)")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    write_png(OUT / "icon.png", render(256, square=True))
    write_png(OUT / "logo.png", render(512, height=256, square=False))


if __name__ == "__main__":
    main()
