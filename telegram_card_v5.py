"""Smooth, font-based Design Polish v5 cards for Shelly and YASNO."""

from __future__ import annotations

import math
import os
import time
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from telegram_card_png import (
    HEIGHT,
    KYIV,
    WIDTH,
    current_measurement,
    valid_forecast,
)

SCALE = 2
BACKGROUND = "#0B1422"
PANEL = "#14243A"
PANEL_LIGHT = "#1B304B"
TEXT = "#F3F7FF"
MUTED = "#A6B9D0"
GREEN = "#3DD5A1"
RED = "#FF7786"
AMBER = "#F3BE69"
BLUE = "#83BBF7"
SLOT_COLORS = {"O": GREEN, "X": RED, "?": AMBER}
PAST_COLORS = {"O": "#22584B", "X": "#654354", "?": "#685540"}
STATUS = {
    "online": ("ОНЛАЙН", GREEN),
    "offline": ("ОФЛАЙН", RED),
    "stale": ("ЗАСТАРІЛІ ДАНІ", AMBER),
    "unknown": ("НЕМАЄ ДАНИХ", AMBER),
}
FONT_FILES = {
    "regular": "opentype/inter/Inter-Regular.otf",
    "medium": "opentype/inter/Inter-Medium.otf",
    "semibold": "opentype/inter/Inter-SemiBold.otf",
}


@lru_cache(maxsize=96)
def font(root: str, weight: str, size: int):
    """Load the Docker-provided Inter face using FreeType."""

    path = Path(root, FONT_FILES[weight])
    if not path.is_file():
        raise ValueError("V5_FONT_MISSING")
    return ImageFont.truetype(str(path), size * SCALE)


class Painter:
    """Paint vector glyphs at 2x and downsample the whole card."""

    def __init__(self):
        """Keep rendering independent of the network and filesystem state."""

        self.image = Image.new(
            "RGB", (WIDTH * SCALE, HEIGHT * SCALE), BACKGROUND
        )
        self.draw = ImageDraw.Draw(self.image)
        self.font_root = os.getenv("TELEGRAM_V5_FONT_ROOT", "/usr/share/fonts")

    def rect(self, box, color, radius=0, border=None, stroke=1):
        """Draw one smooth rounded rectangle in logical pixels."""

        scaled = tuple(round(v * SCALE) for v in box)
        self.draw.rounded_rectangle(
            scaled,
            radius=radius * SCALE,
            fill=color,
            outline=border,
            width=stroke * SCALE,
        )

    def text(self, x, y, value, size, color=TEXT, weight="regular"):
        """Draw actual Unicode glyphs, not bitmap font tiles."""

        face = font(self.font_root, weight, size)
        self.draw.text(
            (x * SCALE, y * SCALE),
            value,
            font=face,
            fill=color,
            anchor="lt",
        )

    def measure(self, value, size, weight="regular"):
        """Report the final-resolution width of the rendered text."""

        face = font(self.font_root, weight, size)
        return self.draw.textlength(value, font=face) / SCALE

    def save(self, path):
        """Write a reproducible 960x800 RGB image with smooth text."""

        reduced = self.image.resize(
            (WIDTH, HEIGHT), resample=Image.Resampling.LANCZOS
        )
        reduced.save(path, "PNG", optimize=True)


def header(painter: Painter, result: dict) -> None:
    """Separate the physical connectivity badge from the outage forecast."""

    painter.rect((34, 24, 926, 112), PANEL, 24)
    painter.text(63, 43, "Електромережа", 30, TEXT, "semibold")
    painter.text(64, 81, "SHELLY  ·  ФАЗА L1", 15, MUTED, "medium")
    status, color = STATUS[result["status"]]
    painter.rect((698, 45, 899, 93), PANEL_LIGHT, 17)
    painter.rect((714, 62, 730, 78), color, 8)
    size = 17 if len(status) <= 12 else 13
    painter.text(743, 58, status, size, color, "semibold")


def live_measurement(painter: Painter, result: dict) -> None:
    """Make voltage readable without displaying old or invalid samples."""

    painter.rect((34, 128, 926, 320), PANEL, 26)
    painter.text(63, 151, "ПОТОЧНА НАПРУГА", 17, MUTED, "medium")
    if result["status"] != "online":
        unavailable_measurement(painter, result["status"])
        return
    volts = f"{result['voltage']:.1f}"
    painter.text(62, 187, volts, 87, TEXT, "semibold")
    x = min(572, 71 + painter.measure(volts, 87, "semibold"))
    painter.text(x, 221, "В", 37, GREEN, "medium")
    painter.rect((63, 295, 230, 300), GREEN, 2)
    painter.text(695, 172, "ЧАСТОТА", 16, MUTED, "medium")
    hz = result.get("frequency")
    label = f"{hz:.2f} Гц" if hz is not None else "—"
    painter.text(695, 207, label, 29, TEXT, "semibold")
    local = datetime.fromtimestamp(result["time"], KYIV)
    painter.text(695, 259, "Оновлено " + local.strftime("%H:%M"), 16, MUTED)


def unavailable_measurement(painter: Painter, status: str) -> None:
    """Never show the previous physical voltage during an outage."""

    painter.text(63, 206, "Напруга недоступна", 43, TEXT, "semibold")
    note = {
        "offline": "Немає зв'язку із Shelly",
        "stale": "Очікуємо нові показання",
    }.get(status, "Немає достовірних показань")
    painter.text(65, 274, note, 19, AMBER, "medium")


def forecast_legend(painter: Painter) -> None:
    """Use compact, smooth keys instead of pixel icons and crossings."""

    for x, marker, caption in (
        (64, "O", "Є світло"),
        (290, "X", "Відключення"),
        (553, "?", "Можливо"),
    ):
        painter.rect((x, 447, x + 15, 462), SLOT_COLORS[marker], 7)
        painter.text(x + 26, 443, caption, 17, MUTED, "medium")


def halfday_track(
    painter: Painter, bars: str, current: int, lane: int
) -> None:
    """Render one 12-hour lane as 24 anti-aliased half-hour blocks."""

    y, first = 499 + lane * 93, lane * 24
    for column in range(24):
        index, x = first + column, 64 + column * 35
        marker = bars[index]
        color = PAST_COLORS[marker] if index < current else SLOT_COLORS[marker]
        painter.rect((x, y, x + 32, y + 38), color, 7)
        if index == current:
            painter.rect(
                (x - 2, y - 2, x + 34, y + 40),
                None,
                9,
                border=TEXT,
                stroke=2,
            )
    for hour in (0, 3, 6, 9, 12):
        x = min(866, 64 + 70 * hour)
        painter.text(x, y + 47, f"{lane * 12 + hour:02d}:00", 14, MUTED)


def current_forecast(bars: str, now: float) -> str:
    """Label schedule-only states without asserting actual electricity."""

    timepoint = datetime.fromtimestamp(now, KYIV)
    index = timepoint.hour * 2 + timepoint.minute // 30
    return {
        "O": "За графіком зараз є світло",
        "X": "За графіком зараз відключення",
        "?": "За графіком можливе відключення",
    }[bars[index]]


def forecast_footer(
    painter: Painter, bars: str, state: dict, now: float
) -> None:
    """Provide bounded forecast totals with a clear provenance label."""

    painter.rect((62, 677, 898, 729), PANEL_LIGHT, 14)
    painter.text(80, 692, current_forecast(bars, now), 19, TEXT, "medium")
    off_hours = f"{bars.count('X') / 2:g}".replace(".", ",")
    painter.text(729, 692, f"План.: {off_hours} год", 16, RED, "semibold")
    checked = datetime.fromtimestamp(state["u"], KYIV).strftime("%H:%M")
    painter.text(
        63,
        746,
        "Лише прогноз YASNO · перевірено " + checked,
        15,
        MUTED,
    )


def forecast_panel(
    painter: Painter, state: dict, bars: str, now: float
) -> None:
    """Draw readable two-lane YASNO slots, never physical power history."""

    painter.rect((34, 336, 926, 777), PANEL, 26)
    painter.text(63, 355, "Графік відключень", 29, TEXT, "semibold")
    day = datetime.fromtimestamp(now, KYIV).strftime("%d.%m.%Y")
    painter.text(63, 404, "YASNO · ДТЕК Київ  ·  " + day, 17, MUTED)
    painter.rect((737, 356, 898, 396), PANEL_LIGHT, 15)
    painter.text(
        754,
        366,
        "Група " + str(state.get("g", "")),
        18,
        BLUE,
        "semibold",
    )
    if not bars:
        painter.text(64, 501, "Актуального графіка немає", 28, MUTED)
        painter.text(64, 550, "Дані YASNO відсутні або застарілі", 18, MUTED)
        return
    forecast_legend(painter)
    current_time = datetime.fromtimestamp(now, KYIV)
    current = current_time.hour * 2 + current_time.minute // 30
    halfday_track(painter, bars, current, 0)
    halfday_track(painter, bars, current, 1)
    forecast_footer(painter, bars, state, now)


def render_card_v5(
    state: dict, target: Path, now: float | None = None
) -> dict:
    """Render an offline-safe modern PNG without contacting any services."""

    if not isinstance(state, dict) or state.get("schema") != 1:
        raise ValueError("invalid widget state")
    clock = time.time() if now is None else float(now)
    if not math.isfinite(clock):
        raise ValueError("invalid clock")
    result = current_measurement(state, clock)
    bars = valid_forecast(state, clock)
    painter = Painter()
    header(painter, result)
    live_measurement(painter, result)
    forecast_panel(painter, state, bars, clock)
    painter.save(target)
    return {
        "style": "v5",
        "status": result["status"],
        "live_voltage_visible": result["status"] == "online",
        "forecast_visible": bool(bars),
        "forecast_off_slots": bars.count("X"),
        "image_bytes": target.stat().st_size,
    }
