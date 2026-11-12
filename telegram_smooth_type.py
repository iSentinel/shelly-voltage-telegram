"""Smooth large Shelly numerals and the Cyrillic voltage unit without fonts."""

from __future__ import annotations

import math

POINTS = {
    "0": (
        (
            (0.25, 0.08),
            (0.72, 0.08),
            (0.84, 0.25),
            (0.84, 0.73),
            (0.72, 0.91),
            (0.25, 0.91),
            (0.13, 0.73),
            (0.13, 0.25),
            (0.25, 0.08),
        ),
    ),
    "1": (((0.22, 0.23), (0.50, 0.08), (0.50, 0.91)),),
    "2": (
        (
            (0.16, 0.22),
            (0.27, 0.09),
            (0.69, 0.08),
            (0.83, 0.22),
            (0.79, 0.39),
            (0.16, 0.90),
            (0.84, 0.90),
        ),
    ),
    "3": (
        (
            (0.18, 0.11),
            (0.70, 0.10),
            (0.83, 0.24),
            (0.74, 0.43),
            (0.52, 0.51),
            (0.74, 0.56),
            (0.83, 0.77),
            (0.68, 0.91),
            (0.17, 0.88),
        ),
    ),
    "4": (
        ((0.71, 0.08), (0.14, 0.64), (0.85, 0.64)),
        ((0.70, 0.08), (0.70, 0.92)),
    ),
    "5": (
        (
            (0.82, 0.09),
            (0.22, 0.09),
            (0.16, 0.49),
            (0.63, 0.49),
            (0.83, 0.62),
            (0.78, 0.81),
            (0.64, 0.91),
            (0.17, 0.89),
        ),
    ),
    "6": (
        (
            (0.76, 0.11),
            (0.43, 0.09),
            (0.18, 0.31),
            (0.15, 0.70),
            (0.29, 0.90),
            (0.69, 0.90),
            (0.84, 0.74),
            (0.76, 0.55),
            (0.22, 0.54),
        ),
    ),
    "7": (((0.15, 0.09), (0.85, 0.09), (0.36, 0.92)),),
    "8": (
        (
            (0.48, 0.08),
            (0.17, 0.23),
            (0.25, 0.45),
            (0.75, 0.55),
            (0.83, 0.76),
            (0.66, 0.92),
            (0.29, 0.92),
            (0.16, 0.76),
            (0.25, 0.55),
            (0.75, 0.45),
            (0.82, 0.23),
            (0.48, 0.08),
        ),
    ),
    "9": (
        (
            (0.76, 0.47),
            (0.26, 0.47),
            (0.15, 0.27),
            (0.30, 0.09),
            (0.69, 0.09),
            (0.84, 0.31),
            (0.81, 0.67),
            (0.60, 0.90),
            (0.22, 0.90),
        ),
    ),
    ".": (((0.48, 0.88), (0.49, 0.89)),),
    "-": (((0.20, 0.52), (0.82, 0.52)),),
    "В": (
        ((0.18, 0.08), (0.18, 0.91)),
        ((0.18, 0.08), (0.63, 0.08), (0.81, 0.23), (0.70, 0.44), (0.18, 0.47)),
        ((0.18, 0.47), (0.69, 0.47), (0.84, 0.68), (0.73, 0.88), (0.18, 0.91)),
    ),
}


def line_distance(px: float, py: float, line: tuple) -> float:
    """Measure one sample to a finite line segment with round end caps."""

    x1, y1, x2, y2 = line
    dx, dy = x2 - x1, y2 - y1
    scale = dx * dx + dy * dy
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / scale))
    return math.hypot(px - x1 - t * dx, py - y1 - t * dy)


def segments(glyph: str, x: int, y: int, width: int, height: int) -> list:
    """Project normalized strokes into display coordinates."""

    result = []
    for stroke in POINTS[glyph]:
        coords = [(x + px * width, y + py * height) for px, py in stroke]
        result.extend((*a, *b) for a, b in zip(coords, coords[1:]))
    return result


def smooth_pixel(
    pixels: bytearray, index: int, color: bytes, coverage: int
) -> None:
    """Blend subpixel coverage into an existing RGB background."""

    base = index * 3
    inverse = 4 - coverage
    for channel in range(3):
        old = pixels[base + channel]
        pixels[base + channel] = (
            old * inverse + color[channel] * coverage + 2
        ) // 4


def draw_glyph(
    pixels: bytearray,
    canvas_width: int,
    glyph: str,
    x: int,
    y: int,
    width: int,
    height: int,
    color: bytes,
) -> None:
    """Rasterize rounded vector strokes with 2x2 antialias samples."""

    lines = segments(glyph, x, y, width, height)
    radius = max(2.1, height * 0.055)
    for row in range(max(0, y), y + height):
        for col in range(max(0, x), x + width):
            cover = 0
            for dy in (0.25, 0.75):
                for dx in (0.25, 0.75):
                    if (
                        min(
                            line_distance(col + dx, row + dy, line)
                            for line in lines
                        )
                        <= radius
                    ):
                        cover += 1
            if cover:
                smooth_pixel(pixels, row * canvas_width + col, color, cover)


def draw_voltage_type(
    pixels: bytearray,
    canvas_width: int,
    voltage: str,
    x: int,
    y: int,
    ink: bytes,
    accent: bytes,
) -> tuple[int, int]:
    """Draw a baseline-aligned voltage and return the unit's anchor."""

    width, height, advance = 42, 64, 53
    for index, char in enumerate(voltage):
        draw_glyph(
            pixels,
            canvas_width,
            char,
            x + index * advance,
            y,
            width,
            height,
            ink,
        )
    unit_x = x + len(voltage) * advance + 5
    unit_y = y + height - 44
    draw_glyph(pixels, canvas_width, "В", unit_x, unit_y, 30, 44, accent)
    return unit_x, unit_y
