"""Render an offline-safe Shelly/YASNO Telegram PNG without dependencies."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from advanced_features import CHART_FONT, write_png
from telegram_schedule_periods import duration_label, schedule_periods
from telegram_smooth_bitmap import smooth_label
from telegram_smooth_type import draw_voltage_type
from telegram_uk_bitmap import UKRAINIAN_FONT
from telegram_widget_data import (
    MAX_FORECAST_AGE,
    MAX_SAMPLE_AGE,
    MIN_VISIBLE_VOLTAGE,
    numeric,
)

WIDTH = 960
HEIGHT = 800
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
OFFLINE_HINT = "ЙМОВІРНЕ ЗНЕСТРУМЛЕННЯ / ЗБІЙ SHELLY ЧИ МЕРЕЖІ"
STATUS_LABELS = {
    "online": "ОНЛАЙН",
    "offline": "ОФЛАЙН",
    "stale": "ЗАСТАРІЛО",
    "unknown": "НЕВІДОМО",
}

FONT = {
    **CHART_FONT,
    **UKRAINIAN_FONT,
    "C": ("01111", "10000", "10000", "10000", "10000", "10000", "01111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "J": ("00111", "00010", "00010", "00010", "10010", "10010", "01100"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "Q": ("01110", "10001", "10001", "10001", "10101", "10010", "01101"),
    "Z": ("11111", "00001", "00010", "00100", "01000", "10000", "11111"),
    "?": ("01110", "10001", "00001", "00010", "00100", "00000", "00100"),
    "(": ("00010", "00100", "01000", "01000", "01000", "00100", "00010"),
    ")": ("01000", "00100", "00010", "00010", "00010", "00100", "01000"),
    "≈": ("00000", "01101", "10010", "00000", "01101", "10010", "00000"),
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
    """Paint softly antialiased UI labels with no extra dependencies."""

    smooth_label(pixels, value, x, y, color, size, FONT, WIDTH, HEIGHT)


def current_measurement(state: dict, now: float) -> dict:
    """Hide old, invalid or offline voltage instead of inventing power."""

    status = state.get("s")
    if status != "online":
        return {
            "status": status if status in ("offline", "stale") else "unknown"
        }
    timestamp = numeric(state.get("t"), 1, now + 2)
    voltage = numeric(state.get("v"), MIN_VISIBLE_VOLTAGE, 400)
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
    label(pixels, "SHELLY / СТАН МЕРЕЖІ", 60, 54, INK, 4)
    status = result["status"]
    color = (
        GREEN if status == "online" else RED if status == "offline" else AMBER
    )
    rectangle(pixels, 690, 42, 900, 93, PANEL_LIGHT, 14)
    rectangle(pixels, 708, 57, 726, 75, color, 9)
    label(pixels, STATUS_LABELS.get(status, "НЕВІДОМО"), 738, 55, color, 3)


def draw_voltage(pixels: bytearray, result: dict, now: float) -> None:
    """Make live voltage prominent and stale voltage invisible."""

    rectangle(pixels, 28, 127, 932, 320, PANEL, 16)
    label(pixels, "НАПРУГА МЕРЕЖІ / L1", 60, 153, MUTED, 3)
    if result["status"] == "online":
        voltage = f"{result['voltage']:.1f}"
        draw_voltage_type(pixels, WIDTH, voltage, 60, 199, INK, GREEN)
        frequency = result.get("frequency")
        freq_text = f"{frequency:.2f} ГЦ" if frequency is not None else "-- ГЦ"
        label(pixels, freq_text, 653, 195, INK, 4)
        last = datetime.fromtimestamp(result["time"], KYIV).strftime(
            "%H:%M:%S"
        )
        label(pixels, "ОНОВЛЕНО " + last, 653, 255, MUTED, 2)
    else:
        draw_voltage_type(pixels, WIDTH, "---", 60, 199, AMBER, AMBER)
        label(pixels, "ДАНІ НЕДОСТУПНІ", 430, 225, MUTED, 3)


def draw_battery(pixels: bytearray, x: int, y: int, kind: str) -> None:
    """Draw red empty and green full battery icons without emoji fonts."""

    color = {"O": GREEN, "X": RED, "?": AMBER}[kind]
    rectangle(pixels, x, y, x + 22, y + 15, color, 3)
    rectangle(pixels, x + 3, y + 3, x + 19, y + 12, PANEL)
    rectangle(pixels, x + 22, y + 4, x + 26, y + 11, color, 1)
    if kind == "O":
        rectangle(pixels, x + 5, y + 5, x + 17, y + 10, GREEN)
    elif kind == "?":
        rectangle(pixels, x + 10, y + 4, x + 12, y + 9, AMBER)


def draw_schedule_rows(
    pixels: bytearray, periods: list[tuple[str, str, str, int]]
) -> None:
    """Draw five chronological forecast windows; the mini-bar shows all."""

    names = {
        "X": "ПЛАНОВЕ ВІДКЛЮЧЕННЯ",
        "O": "ЗА ГРАФІКОМ Є СВІТЛО",
        "?": "МОЖЛИВЕ ВІДКЛЮЧЕННЯ",
    }
    shown = periods[:5] if len(periods) <= 5 else periods[:4]
    for index, (kind, begin, end, minutes) in enumerate(shown):
        y = 588 + 30 * index
        draw_battery(pixels, 60, y + 2, kind)
        interval = f"{begin} - {end} ({duration_label(minutes)})"
        label(pixels, interval, 104, y, INK, 3)
        label(
            pixels,
            names[kind],
            604,
            y,
            {"X": RED, "O": GREEN, "?": AMBER}[kind],
            2,
        )
    if len(periods) > 5:
        label(pixels, f"ЩЕ {len(periods) - 4} ІНТЕРВАЛІВ", 104, 714, AMBER, 3)


def strike_past_tile(pixels: bytearray, x: int, y: int, color: bytes) -> None:
    """Dim one completed half-hour and draw a thin upward strike."""

    faded = bytes(
        (part * 42 + panel * 58) // 100 for part, panel in zip(color, PANEL)
    )
    rectangle(pixels, x, y, x + 66, y + 40, faded, 5)
    for step in range(54):
        col = x + 6 + step
        row = y + 34 - (step * 29 // 53)
        rectangle(pixels, col, row, col + 2, row + 1, MUTED)


def outline_current_tile(pixels: bytearray, x: int, y: int) -> None:
    """Mark the active half-hour without changing the forecast color."""

    rectangle(pixels, x + 2, y + 1, x + 64, y + 3, AMBER)
    rectangle(pixels, x + 2, y + 37, x + 64, y + 39, AMBER)
    rectangle(pixels, x + 1, y + 3, x + 3, y + 37, AMBER)
    rectangle(pixels, x + 63, y + 3, x + 65, y + 37, AMBER)


def draw_hour_tiles(pixels: bytearray, bars: str, now: float) -> None:
    """Differentiate completed, active and future half-hour forecast slots."""

    colors = {"O": GREEN, "X": RED, "?": AMBER}
    local = datetime.fromtimestamp(now, KYIV)
    current = local.hour * 2 + local.minute // 30
    for hour in range(24):
        x, y = 60 + 140 * (hour % 6), 383 + 45 * (hour // 6)
        first, second = bars[2 * hour : 2 * hour + 2]
        if first == second:
            rectangle(pixels, x, y, x + 132, y + 40, colors[first], 7)
        else:
            rectangle(pixels, x, y, x + 66, y + 40, colors[first], 5)
            rectangle(pixels, x + 66, y, x + 132, y + 40, colors[second], 5)
        for half, kind in enumerate((first, second)):
            index = hour * 2 + half
            tile_x = x + half * 66
            if index < current:
                strike_past_tile(pixels, tile_x, y, colors[kind])
            elif index == current:
                outline_current_tile(pixels, tile_x, y)
        label(pixels, f"{hour:02d}:00", x + 20, y + 9, INK, 3)


def half_hour_total(slots: int) -> str:
    """Format an exact number of 30-minute YASNO slots, never round it."""

    if not 0 <= slots <= 48:
        raise ValueError("invalid number of forecast slots")
    hours = str(slots // 2)
    if slots % 2:
        hours += ",5"
    return hours + " ГОД"


def draw_forecast_legend(pixels: bytearray, state: dict) -> None:
    """Explain the schedule colors and identify the source update clock."""

    for x, kind, name in (
        (60, "O", "СВІТЛО"),
        (244, "X", "ВІДКЛЮЧЕННЯ"),
        (505, "?", "МОЖЛИВО"),
    ):
        color = {"O": GREEN, "X": RED, "?": AMBER}[kind]
        rectangle(pixels, x, 564, x + 15, 578, color, 3)
        label(pixels, name, x + 24, 564, MUTED, 2)
    updated = datetime.fromtimestamp(state["u"], KYIV).strftime("%H:%M")
    label(pixels, "ПЕРЕВІРЕНО " + updated, 722, 564, MUTED, 2)


def draw_forecast_totals(pixels: bytearray, bars: str) -> None:
    """Present all 24 forecast hours without treating them as telemetry."""

    for x, kind, name in (
        (60, "O", "СВІТЛО: "),
        (342, "X", "НЕМАЄ: "),
        (651, "?", "МОЖЛИВО: "),
    ):
        minutes = half_hour_total(bars.count(kind))
        color = {"O": GREEN, "X": RED, "?": AMBER}[kind]
        label(pixels, name + minutes, x, 739, color, 2)


def draw_timeline(
    pixels: bytearray, state: dict, bars: str, now: float
) -> None:
    """Combine 48 colored slots with explicit batteries and durations."""

    rectangle(pixels, 28, 336, 932, 788, PANEL, 16)
    label(pixels, "ГРАФІК YASNO / ДТЕК", 60, 347, INK, 3)
    if not bars:
        label(pixels, "НЕМАЄ АКТУАЛЬНОГО ГРАФІКА", 60, 572, MUTED, 3)
        hint = OFFLINE_HINT if state.get("s") == "offline" else "НЕМАЄ ДАНИХ"
        label(pixels, hint, 60, 758, MUTED, 2)
        return
    local_day = datetime.fromtimestamp(now, KYIV).strftime("%d.%m.%Y")
    label(pixels, local_day, 516, 352, MUTED, 2)
    label(pixels, "ГРУПА " + str(state["g"]), 734, 347, BLUE, 3)
    draw_hour_tiles(pixels, bars, now)
    draw_forecast_legend(pixels, state)
    draw_schedule_rows(pixels, schedule_periods(bars))
    draw_forecast_totals(pixels, bars)
    hint = (
        "ЛИШЕ ПРОГНОЗ / " + OFFLINE_HINT
        if state.get("s") == "offline"
        else "ЛИШЕ ПРОГНОЗ / ФАКТ ЗА SHELLY"
    )
    label(pixels, hint, 60, 758, MUTED, 2)


def render_card_png(
    state: dict,
    target: Path,
    now: float | None = None,
) -> dict:
    """Create one 960x800 PNG and return non-sensitive rendering metadata."""

    style = os.getenv("TELEGRAM_GRAPHIC_STYLE", "v4")
    if style == "v5":
        from telegram_card_v5 import render_card_v5

        return render_card_v5(state, target, now)
    if style != "v4":
        raise ValueError("unsupported graphic style")
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
