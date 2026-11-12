"""Cache softly antialiased 5x7 glyph masks without external font packages."""

from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=640)
def smooth_mask(glyph: tuple[str, ...], size: int) -> tuple:
    """Build a soft 3x3 coverage mask for one safely bounded glyph."""

    if not 1 <= size <= 12 or len(glyph) != 7:
        raise ValueError("invalid glyph size")
    width, height = 5 * size, 7 * size
    active = [
        [glyph[y // size][x // size] == "1" for x in range(width)]
        for y in range(height)
    ]

    def bit(x: int, y: int) -> int:
        """Return one glyph-pixel occupancy with a transparent border."""

        if 0 <= x < width and 0 <= y < height:
            return int(active[y][x])
        return 0

    masks = []
    for y in range(height):
        for x in range(width):
            coverage = (
                bit(x, y) * 4
                + (bit(x - 1, y) + bit(x + 1, y)) * 2
                + (bit(x, y - 1) + bit(x, y + 1)) * 2
                + bit(x - 1, y - 1)
                + bit(x + 1, y - 1)
                + bit(x - 1, y + 1)
                + bit(x + 1, y + 1)
            )
            if coverage:
                masks.append((x, y, coverage))
    return tuple(masks)


def smooth_label(
    pixels: bytearray,
    text: str,
    x: int,
    y: int,
    color: bytes,
    size: int,
    font: dict,
    width: int,
    height: int,
) -> None:
    """Blend cached glyph masks into the existing RGB image."""

    for char in text.upper()[:85]:
        glyph = font.get(char, font[" "])
        for dx, dy, coverage in smooth_mask(glyph, size):
            col, row = x + dx, y + dy
            if not 0 <= col < width or not 0 <= row < height:
                continue
            start = (row * width + col) * 3
            if coverage == 16:
                pixels[start : start + 3] = color
                continue
            for channel in range(3):
                old = pixels[start + channel]
                pixels[start + channel] = (
                    old * (16 - coverage) + color[channel] * coverage + 8
                ) // 16
        x += 6 * size
