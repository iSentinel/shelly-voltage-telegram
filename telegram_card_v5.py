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
from telegram_schedule_periods import duration_label, schedule_periods
from telegram_widget_data import numeric

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
STATE_COLORS = {
    "online": GREEN,
    "offline": RED,
    "stale": AMBER,
    "unknown": AMBER,
    "partial": AMBER,
}
QUALITY_COLORS = {
    "normal": GREEN,
    "low": AMBER,
    "high": AMBER,
    "critical_low": RED,
    "critical_high": RED,
    "unknown": MUTED,
}
CARD_TEXT = {
    "uk": {
        "title": "Електромережа",
        "voltage": "ПОТОЧНА НАПРУГА",
        "online": "НА ЗВ'ЯЗКУ",
        "offline": "НЕМАЄ ЗВ'ЯЗКУ",
        "stale": "ЗАСТАРІЛІ ДАНІ",
        "unknown": "НЕМАЄ ДАНИХ",
        "partial": "ЧАСТКОВІ ДАНІ",
        "normal": "Напруга в нормі",
        "low": "Знижена напруга",
        "high": "Підвищена напруга",
        "critical_low": "Критично низька",
        "critical_high": "Критично висока",
        "quality_unknown": "Пороги недоступні",
        "unavailable": "Напруга недоступна",
        "frequency": "ЧАСТОТА",
        "volts": "В",
        "hertz": "Гц",
        "measured": "Виміряно",
        "forecast": "Графік відключень",
        "source": "YASNO · ДТЕК Київ",
        "group": "Група {group}",
        "no_forecast": "Актуального графіка немає",
        "checked": "Лише прогноз YASNO · перевірено {time}",
        "O": "Є світло",
        "X": "Відключення",
        "?": "Можливо",
        "period_O": "ЗА ГРАФІКОМ Є СВІТЛО",
        "period_X": "ПЛАНОВЕ ВІДКЛЮЧЕННЯ",
        "period_?": "МОЖЛИВЕ ВІДКЛЮЧЕННЯ",
        "extra": "ЩЕ {count} ІНТЕРВАЛІВ",
    },
    "ru": {
        "title": "Электросеть",
        "voltage": "ТЕКУЩЕЕ НАПРЯЖЕНИЕ",
        "online": "НА СВЯЗИ",
        "offline": "НЕТ СВЯЗИ",
        "stale": "ДАННЫЕ УСТАРЕЛИ",
        "unknown": "НЕТ ДАННЫХ",
        "partial": "ЧАСТИЧНЫЕ ДАННЫЕ",
        "normal": "Напряжение в норме",
        "low": "Пониженное напряжение",
        "high": "Повышенное напряжение",
        "critical_low": "Критически низкое",
        "critical_high": "Критически высокое",
        "quality_unknown": "Пороги недоступны",
        "unavailable": "Напряжение недоступно",
        "frequency": "ЧАСТОТА",
        "volts": "В",
        "hertz": "Гц",
        "measured": "Измерено",
        "forecast": "График отключений",
        "source": "YASNO · ДТЭК Киев",
        "group": "Группа {group}",
        "no_forecast": "Актуального графика нет",
        "checked": "Только прогноз YASNO · проверено {time}",
        "O": "Есть свет",
        "X": "Отключение",
        "?": "Возможно",
        "period_O": "СВЕТ ПО ГРАФИКУ",
        "period_X": "ПЛАНОВОЕ ОТКЛЮЧЕНИЕ",
        "period_?": "ВОЗМОЖНОЕ ОТКЛЮЧЕНИЕ",
        "extra": "ЕЩЁ {count} ИНТЕРВАЛОВ",
    },
    "en": {
        "title": "Power monitor",
        "voltage": "LIVE VOLTAGE",
        "online": "CONNECTED",
        "offline": "OFFLINE",
        "stale": "STALE DATA",
        "unknown": "NO DATA",
        "partial": "PARTIAL DATA",
        "normal": "Voltage in range",
        "low": "Low voltage",
        "high": "High voltage",
        "critical_low": "Critically low",
        "critical_high": "Critically high",
        "quality_unknown": "Thresholds unavailable",
        "unavailable": "Voltage unavailable",
        "frequency": "FREQUENCY",
        "volts": "V",
        "hertz": "Hz",
        "measured": "Measured",
        "forecast": "Outage schedule",
        "source": "YASNO · DTEK Kyiv",
        "group": "Group {group}",
        "no_forecast": "Current schedule unavailable",
        "checked": "YASNO forecast only · checked {time}",
        "O": "Power scheduled",
        "X": "Scheduled off",
        "?": "Possible outage",
        "period_O": "POWER SCHEDULED",
        "period_X": "SCHEDULED OUTAGE",
        "period_?": "POSSIBLE OUTAGE",
        "extra": "{count} MORE PERIODS",
    },
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

    def __init__(self, language: str = "uk"):
        """Keep rendering independent of the network and filesystem state."""

        self.language = language
        self.labels = CARD_TEXT[language]
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

    def diagonal_past_slot(self, x, y):
        """Cross a completed half-hour diagonally, inside its own tile."""

        self.draw.line(
            (
                ((x + 6) * SCALE, (y + 29) * SCALE),
                ((x + 59) * SCALE, (y + 6) * SCALE),
            ),
            fill=MUTED,
            width=2 * SCALE,
        )

    def strike(self, x, y, width, size):
        """Cross only completed time text at double-resolution scale."""

        center = round((y + size * 0.53) * SCALE)
        self.draw.line(
            (
                (round(x * SCALE), center),
                (round((x + width) * SCALE), center),
            ),
            fill=MUTED,
            width=2 * SCALE,
        )

    def save(self, path):
        """Write a reproducible 960x800 RGB image with smooth text."""

        reduced = self.image.resize(
            (WIDTH, HEIGHT), resample=Image.Resampling.LANCZOS
        )
        reduced.save(path, "PNG", optimize=True)


def voltage_quality(voltage: float, limits: object) -> str:
    """Classify only with valid thresholds supplied by the running bot."""

    if not isinstance(limits, (list, tuple)) or len(limits) != 4:
        return "unknown"
    values = [numeric(value, 0, 500) for value in limits]
    if any(value is None for value in values) or values != sorted(values):
        return "unknown"
    critical_low, warning_low, warning_high, critical_high = values
    if voltage <= critical_low:
        return "critical_low"
    if voltage >= critical_high:
        return "critical_high"
    if voltage <= warning_low:
        return "low"
    if voltage >= warning_high:
        return "high"
    return "normal"


def phase_measurements(state: dict, now: float) -> dict:
    """Validate each phase independently before rendering any value."""

    secondary = state.get("p")
    phases = {"L": state}
    if isinstance(secondary, dict) and set(secondary) == {"L2", "L3"}:
        phases = {"L1": state, **secondary}
    result = {}
    for phase, source in phases.items():
        data = current_measurement(
            source if isinstance(source, dict) else {}, now
        )
        if data["status"] == "online":
            data["quality"] = voltage_quality(
                data["voltage"], state.get("limits")
            )
        result[phase] = data
    return result


def header(painter: Painter, phases: dict) -> None:
    """Show meter connectivity separately from electrical severity."""

    painter.rect((34, 24, 926, 112), PANEL, 24)
    painter.text(63, 41, painter.labels["title"], 34, TEXT, "semibold")
    painter.text(64, 80, "SHELLY  ·  " + " / ".join(phases), 19, MUTED)
    statuses = {result["status"] for result in phases.values()}
    status = next(iter(statuses)) if len(statuses) == 1 else "partial"
    color = STATE_COLORS[status]
    painter.rect((628, 45, 899, 93), PANEL_LIGHT, 17)
    painter.rect((644, 62, 660, 78), color, 8)
    painter.text(673, 59, painter.labels[status], 20, color, "semibold")


def quality_label(painter: Painter, result: dict) -> tuple[str, str]:
    """Provide both a severity label and color for a fresh measurement."""

    quality = result["quality"]
    key = "quality_unknown" if quality == "unknown" else quality
    return painter.labels[key], QUALITY_COLORS[quality]


def frequency_text(painter: Painter, result: dict) -> str:
    """Format known meter frequency using the selected language's units."""

    hz = result.get("frequency")
    return f"{hz:.2f} {painter.labels['hertz']}" if hz is not None else "—"


def live_measurement(painter: Painter, result: dict) -> None:
    """Show one large voltage and explicit measured severity."""

    painter.rect((34, 128, 926, 320), PANEL, 26)
    painter.text(63, 151, painter.labels["voltage"], 22, MUTED, "medium")
    status = result["status"]
    if status != "online":
        painter.text(
            63, 204, painter.labels["unavailable"], 43, TEXT, "semibold"
        )
        painter.text(64, 269, painter.labels[status], 23, STATE_COLORS[status])
        return
    label, color = quality_label(painter, result)
    volts = f"{result['voltage']:.1f}"
    painter.text(62, 183, volts, 85, color, "semibold")
    x = 75 + painter.measure(volts, 85, "semibold")
    painter.text(x, 214, painter.labels["volts"], 35, color, "medium")
    painter.text(64, 279, label, 25, color, "semibold")
    painter.text(650, 159, painter.labels["frequency"], 20, MUTED, "medium")
    painter.text(
        650, 200, frequency_text(painter, result), 32, TEXT, "semibold"
    )
    local = datetime.fromtimestamp(result["time"], KYIV)
    when = f"{painter.labels['measured']} {local:%H:%M}"
    painter.text(650, 267, when, 20, MUTED)


def phase_column(painter: Painter, phase: str, result: dict, x: int) -> None:
    """Show one phase with its independent voltage and severity."""

    painter.rect((x, 140, x + 278, 310), PANEL_LIGHT, 19)
    painter.text(x + 16, 154, phase, 24, TEXT, "semibold")
    status = result["status"]
    if status != "online":
        painter.text(x + 16, 199, "—", 50, MUTED, "semibold")
        painter.text(
            x + 16, 263, painter.labels[status], 20, STATE_COLORS[status]
        )
        return
    label, color = quality_label(painter, result)
    volts = f"{result['voltage']:.1f}"
    painter.text(x + 16, 190, volts, 47, color, "semibold")
    unit_x = x + 25 + painter.measure(volts, 47, "semibold")
    painter.text(unit_x, 204, painter.labels["volts"], 26, color)
    painter.text(x + 16, 246, label, 20, color, "semibold")
    local = datetime.fromtimestamp(result["time"], KYIV)
    details = f"{frequency_text(painter, result)}  ·  {local:%H:%M}"
    painter.text(x + 16, 280, details, 19, TEXT)


def forecast_legend(painter: Painter) -> None:
    """Explain each forecast state without confusing it with a meter."""

    for x, marker, caption in (
        (64, "O", painter.labels["O"]),
        (344, "X", painter.labels["X"]),
        (624, "?", painter.labels["?"]),
    ):
        painter.rect((x, 580, x + 13, 593), SLOT_COLORS[marker], 6)
        painter.text(x + 21, 577, caption, 21, TEXT, "medium")


def hourly_tile(painter: Painter, bars: str, hour: int, current: int) -> None:
    """Render one labelled hour with both independent half-hour states."""

    x = 64 + 140 * (hour % 6)
    y = 413 + 40 * (hour // 6)
    for half in range(2):
        index = hour * 2 + half
        left = x + half * 65
        kind = bars[index]
        color = PAST_COLORS[kind] if index < current else SLOT_COLORS[kind]
        painter.rect((left, y, left + 65, y + 35), color, 5)
        if index < current:
            painter.diagonal_past_slot(left, y)
        elif index == current:
            painter.rect(
                (left + 1, y + 1, left + 64, y + 34),
                None,
                5,
                border=TEXT,
                stroke=2,
            )
    hour_label = f"{hour:02d}:00"
    advance = painter.measure(hour_label, 23, "semibold")
    painter.rect((x + 24, y + 4, x + 106, y + 31), BACKGROUND, 7)
    text_x = x + (130 - advance) / 2
    completed = (hour + 1) * 2 <= current
    painter.text(
        text_x,
        y + 8,
        hour_label,
        23,
        MUTED if completed else TEXT,
        "semibold",
    )
    if completed:
        painter.strike(text_x, y + 8, advance, 23)


def visible_periods(bars: str) -> list[tuple[str, str, str, int]]:
    """Retain up to six ordered forecast periods with an overflow marker."""

    intervals = schedule_periods(bars)
    return intervals if len(intervals) <= 6 else intervals[:5]


def forecast_periods(painter: Painter, bars: str, minute: int) -> None:
    """Strike elapsed windows; keep the current and future rows legible."""

    rows = visible_periods(bars)
    end_minute = 0
    for index, (kind, start, end, minutes) in enumerate(rows):
        y = 608 + 24 * index
        painter.rect((65, y + 4, 79, y + 18), SLOT_COLORS[kind], 4)
        duration = duration_label(minutes, painter.language)
        window = f"{start} – {end} ({duration})"
        end_minute += minutes
        completed = end_minute <= minute
        painter.text(92, y, window, 22, MUTED if completed else TEXT, "medium")
        if completed:
            painter.strike(92, y, painter.measure(window, 22, "medium"), 22)
        name = painter.labels["period_" + kind]
        painter.text(592, y + 1, name, 18, SLOT_COLORS[kind])
    extra = len(schedule_periods(bars)) - len(rows)
    if extra:
        painter.text(
            92,
            608 + 24 * len(rows),
            painter.labels["extra"].format(count=extra),
            22,
            AMBER,
            "semibold",
        )


def forecast_panel(
    painter: Painter, state: dict, bars: str, now: float
) -> None:
    """Keep all 24 labelled hours and the textual period breakdown."""

    painter.rect((34, 336, 926, 787), PANEL, 26)
    painter.text(63, 352, painter.labels["forecast"], 30, TEXT, "semibold")
    day = datetime.fromtimestamp(now, KYIV).strftime("%d.%m.%Y")
    painter.text(63, 387, f"{painter.labels['source']}  ·  {day}", 19, MUTED)
    if not bars:
        painter.text(64, 476, painter.labels["no_forecast"], 34, MUTED)
        return
    painter.rect((737, 356, 898, 396), PANEL_LIGHT, 15)
    group = painter.labels["group"].format(group=state["g"])
    painter.text(754, 366, group, 20, BLUE, "semibold")
    local = datetime.fromtimestamp(now, KYIV)
    current = local.hour * 2 + local.minute // 30
    for hour in range(24):
        hourly_tile(painter, bars, hour, current)
    forecast_legend(painter)
    forecast_periods(painter, bars, local.hour * 60 + local.minute)
    checked = datetime.fromtimestamp(state["u"], KYIV).strftime("%H:%M")
    painter.text(
        64,
        764,
        painter.labels["checked"].format(time=checked),
        17,
        MUTED,
    )


def render_card_v5(
    state: dict, target: Path, now: float | None = None
) -> dict:
    """Render an offline-safe modern PNG without contacting any services."""

    if not isinstance(state, dict) or state.get("schema") != 1:
        raise ValueError("invalid widget state")
    clock = time.time() if now is None else float(now)
    if not math.isfinite(clock):
        raise ValueError("invalid clock")
    language = state.get("l")
    if not isinstance(language, str) or language not in CARD_TEXT:
        language = "uk"
    phases = phase_measurements(state, clock)
    primary = next(iter(phases.values()))
    bars = valid_forecast(state, clock)
    painter = Painter(language)
    header(painter, phases)
    if len(phases) == 1:
        live_measurement(painter, primary)
    else:
        for slot, (phase, result) in enumerate(phases.items()):
            phase_column(painter, phase, result, 47 + 294 * slot)
    forecast_panel(painter, state, bars, clock)
    painter.save(target)
    return {
        "style": "v5",
        "status": primary["status"],
        "live_voltage_visible": primary["status"] == "online",
        "phases": {phase: data["status"] for phase, data in phases.items()},
        "forecast_visible": bool(bars),
        "forecast_off_slots": bars.count("X"),
        "image_bytes": target.stat().st_size,
    }
