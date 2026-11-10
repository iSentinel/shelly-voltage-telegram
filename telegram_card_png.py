"""Render an offline-safe Shelly/YASNO Telegram PNG without dependencies."""

from __future__ import annotations

import argparse
import json
import math
import re
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from advanced_features import CHART_FONT, write_png
from telegram_widget_data import MAX_FORECAST_AGE, MAX_SAMPLE_AGE, numeric

WIDTH = 960
HEIGHT = 560
KYIV = ZoneInfo("Europe/Kyiv")
INK = b"\xed\xf5\xff"
MUTED = b"\x9c\xaf\xc3"
NAVY = b"\x0c\x15\x24"
PANEL = b"\x16\x25\x3a"
PANEL_LIGHT = b"\x20\x33\x4b"
GREEN = b"\x35\xd0\x92"
RED = b"\xff\x70\x7a"
AMBER = b"\xff\xc3\x64"
BLUE = b"\x66\xb1\xfa"
OFFLINE_HINT = "POWER OUTAGE LIKELY / NOT CONFIRMED"

FONT = {
    **CHART_FONT,
    "C": ("01111", "10000", "10000", "10000", "10000", "10000", "01111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "J": ("00111", "00010", "00010", "00010", "10010", "10010", "01100"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "Q": ("01110", "10001", "10001", "10001", "10101", "10010", "01101"),
    "Z": ("11111", "00001", "00010", "00100", "01000", "10000", "11111"),
    "?": ("01110", "10001", "00001", "00010", "00100", "00000", "00100"),
    "=": ("00000", "11111", "00000", "11111", "00000", "00000", "00000"),
}


def rectangle(
    pixels: bytearray,
    x: int,
    y: int,
    right: int,
    bottom: int,
    color: bytes,
    radius: int = 0,
) -> None:
    """Fill a clipped, optionally rounded region with one RGB color."""

    left = max(0, x)
    top = max(0, y)
    for row in range(top, min(HEIGHT, bottom)):
        inset = 0
        edge = min(row - y, bottom - row - 1)
        if radius and edge < radius:
            offset = radius - edge - 1
            inset = math.ceil(
                radius - math.sqrt(radius * radius - offset * offset)
            )
        first = max(left, x + inset)
        last = min(WIDTH, right - inset)
        if first < last:
            start = (row * WIDTH + first) * 3
            pixels[start : start + (last - first) * 3] = color * (last - first)


def label(
    pixels: bytearray,
    value: str,
    x: int,
    y: int,
    color: bytes = INK,
    size: int = 3,
) -> None:
    """Paint a bounded ASCII glyph label in the image."""

    for char in value.upper()[:85]:
        glyph = FONT.get(char, FONT[" "])
        for row, line in enumerate(glyph):
            for col, enabled in enumerate(line):
                if enabled == "1":
                    rectangle(
                        pixels,
                        x + col * size,
                        y + row * size,
                        x + (col + 1) * size,
                        y + (row + 1) * size,
                        color,
                    )
        x += 6 * size


def current_measurement(state: dict, now: float) -> dict:
    """Hide old, invalid or offline voltage instead of inventing power."""

    status = state.get("s")
    if status != "online":
        return {
            "status": status if status in ("offline", "stale") else "unknown"
        }
    timestamp = numeric(state.get("t"), 1, now + 2)
    voltage = numeric(state.get("v"), 0, 400)
    if timestamp is None or not 0 <= now - timestamp <= MAX_SAMPLE_AGE:
        return {"status": "stale"}
    if voltage is None:
        return {"status": "unknown"}
    frequency = numeric(state.get("f"), 40, 70)
    return {
        "status": "online",
        "voltage": voltage,
        "frequency": frequency,
        "time": timestamp,
    }


def valid_forecast(state: dict, now: float) -> str:
    """Accept a verified, unexpired forecast only for today's Kyiv date."""

    bars = state.get("b")
    if not isinstance(bars, str) or len(bars) != 48:
        return ""
    if set(bars) - set("OX?"):
        return ""
    if state.get("src") != "yasno":
        return ""
    if not re.fullmatch(r"\d{1,2}\.\d", str(state.get("g", ""))):
        return ""
    day = datetime.fromtimestamp(now, KYIV).date().isoformat()
    if state.get("d") != day:
        return ""
    updated = numeric(state.get("u"), 1, now + 2)
    expires = numeric(state.get("e"), now + 0.01, now + 172800)
    if updated is None or expires is None:
        return ""
    if not 0 <= now - updated <= MAX_FORECAST_AGE:
        return ""
    return bars


def draw_header(pixels: bytearray, result: dict) -> None:
    """Render the device identity and measured connectivity state."""

    rectangle(pixels, 28, 24, 932, 112, PANEL, 16)
    label(pixels, "SHELLY  /  GRID STATUS", 60, 54, INK, 4)
    status = result["status"]
    color = (
        GREEN if status == "online" else RED if status == "offline" else AMBER
    )
    rectangle(pixels, 690, 42, 900, 93, PANEL_LIGHT, 14)
    rectangle(pixels, 708, 57, 726, 75, color, 9)
    label(pixels, status, 746, 55, color, 3)


def draw_voltage(pixels: bytearray, result: dict, now: float) -> None:
    """Make live voltage prominent and stale voltage invisible."""

    rectangle(pixels, 28, 127, 932, 320, PANEL, 16)
    label(pixels, "MEASURED VOLTAGE  /  L1", 60, 153, MUTED, 3)
    if result["status"] == "online":
        voltage = f"{result['voltage']:.1f}"
        label(pixels, voltage, 60, 199, INK, 9)
        label(pixels, "V", 350, 230, GREEN, 6)
        frequency = result.get("frequency")
        freq_text = f"{frequency:.2f} HZ" if frequency is not None else "-- HZ"
        label(pixels, freq_text, 653, 195, INK, 4)
        last = datetime.fromtimestamp(result["time"], KYIV).strftime(
            "%H:%M:%S"
        )
        label(pixels, "LAST " + last, 653, 255, MUTED, 2)
    else:
        label(pixels, "--- V", 60, 205, AMBER, 8)
        label(pixels, "CURRENT VALUE UNKNOWN", 430, 225, MUTED, 3)


def draw_timeline(
    pixels: bytearray, state: dict, bars: str, now: float
) -> None:
    """Draw precisely 48 half-hour slots, never infer live electricity."""

    rectangle(pixels, 28, 336, 932, 520, PANEL, 16)
    label(pixels, "YASNO / DTEK FORECAST", 60, 362, INK, 3)
    if not bars:
        label(pixels, "NO CURRENT FORECAST", 60, 414, MUTED, 3)
        hint = OFFLINE_HINT if state.get("s") == "offline" else "NO LIVE DATA"
        label(pixels, hint, 60, 469, MUTED, 2)
        return
    label(pixels, "GROUP " + str(state["g"]), 734, 362, BLUE, 3)
    colors = {"O": GREEN, "X": RED, "?": AMBER}
    for i, symbol in enumerate(bars):
        x = 60 + i * 17
        rectangle(pixels, x, 414, x + 15, 447, colors[symbol], 3)
    for i, value in enumerate(("00:00", "06:00", "12:00", "18:00", "24:00")):
        label(pixels, value, 60 + i * 199, 457, MUTED, 2)
    hint = (
        "FORECAST ONLY  /  " + OFFLINE_HINT
        if state.get("s") == "offline"
        else "FORECAST ONLY  /  SHELLY IS LIVE DATA"
    )
    label(pixels, hint, 60, 491, MUTED, 2)


def render_card_png(
    state: dict,
    target: Path,
    now: float | None = None,
) -> dict:
    """Create one 960x560 PNG and return non-sensitive rendering metadata."""

    if not isinstance(state, dict) or state.get("schema") != 1:
        raise ValueError("invalid widget state")
    clock = time.time() if now is None else float(now)
    if not math.isfinite(clock):
        raise ValueError("invalid clock")
    result = current_measurement(state, clock)
    bars = valid_forecast(state, clock)
    pixels = bytearray(NAVY * WIDTH * HEIGHT)
    draw_header(pixels, result)
    draw_voltage(pixels, result, clock)
    draw_timeline(pixels, state, bars, clock)
    write_png(target, WIDTH, HEIGHT, pixels)
    return {
        "status": result["status"],
        "live_voltage_visible": result["status"] == "online",
        "forecast_visible": bool(bars),
        "forecast_off_slots": bars.count("X"),
        "image_bytes": target.stat().st_size,
    }


def main() -> None:
    """Render an explicit local state file; never access Telegram or Shelly."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--now", type=float)
    args = parser.parse_args()
    if args.state_file.stat().st_size > 4096:
        parser.error("state input too large")
    state = json.loads(args.state_file.read_text(encoding="utf-8"))
    print(json.dumps(render_card_png(state, args.output, args.now)))


if __name__ == "__main__":
    main()
