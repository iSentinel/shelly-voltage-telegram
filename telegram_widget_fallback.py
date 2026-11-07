"""Render a stale-safe Telegram text caption for unsupported widget clients."""

from __future__ import annotations

import json
import math
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from outage_data_ua import windows

KYIV = ZoneInfo("Europe/Kyiv")
WORDS = {
    "uk": (
        "Напруга",
        "На зв'язку",
        "Немає зв'язку",
        "Дані застаріли",
        "Поточний стан невідомий",
        "Частота",
        "Прогноз",
        "група",
        "Відключення",
        "Можливо",
        "Неофіційне джерело",
        "Це прогноз, не вимірювання",
    ),
    "ru": (
        "Напряжение",
        "На связи",
        "Нет связи",
        "Данные устарели",
        "Текущее состояние неизвестно",
        "Частота",
        "Прогноз",
        "группа",
        "Отключение",
        "Возможно",
        "Неофициальный источник",
        "Это прогноз, не измерение",
    ),
    "en": (
        "Voltage",
        "Connected",
        "Disconnected",
        "Data expired",
        "Actual supply unknown",
        "Frequency",
        "Forecast",
        "group",
        "Outage",
        "Possible",
        "Unofficial source",
        "Forecast, not measurement",
    ),
}


def bounded(value: object, minimum: float, maximum: float) -> bool:
    """Accept finite numeric values within the documented meter range."""

    return (
        not isinstance(value, bool)
        and isinstance(value, (float, int))
        and math.isfinite(value)
        and minimum <= value <= maximum
    )


def measured_lines(
    data: dict, now: float, words: tuple[str, ...]
) -> list[str]:
    """Never render an aged or disconnected voltage as a live value."""

    raw_state = data.get("s")
    if raw_state == "online":
        ts, voltage = data.get("t"), data.get("v")
        if (
            bounded(ts, 1, now)
            and 0 <= now - ts <= 60
            and bounded(voltage, 0, 400)
        ):
            lines = [f"⚡ {words[0]}: {voltage:.1f} В"]
            frequency = data.get("f")
            if bounded(frequency, 40, 70):
                lines.append(f"{words[5]}: {frequency:.1f} Гц")
            return lines
        return [f"⚠️ {words[3]}. {words[4]}."]
    if raw_state == "offline":
        return [f"🔴 {words[2]}. {words[4]}."]
    return [f"⚠️ {words[4]}."]


def valid_forecast(data: dict, now: float) -> bool:
    """Prevent old schedules from appearing as current captions."""

    bars = data.get("b")
    if not isinstance(bars, str) or not re.fullmatch("[OX?]{48}", bars):
        return False
    updated, expires = data.get("u"), data.get("e")
    if not bounded(updated, 1, now) or not bounded(
        expires, now, now + 30 * 3600
    ):
        return False
    if not 0 <= now - updated <= 7200 or data.get("src") != "community":
        return False
    local_date = datetime.fromtimestamp(now, KYIV).date().isoformat()
    return data.get("d") == local_date


def forecast_lines(
    data: dict, now: float, words: tuple[str, ...]
) -> list[str]:
    """Show only a bounded list of certain and tentative outage windows."""

    if not valid_forecast(data, now):
        return []
    group = data.get("g")
    if not isinstance(group, str) or not re.fullmatch(r"\d{1,2}\.\d", group):
        return []
    codes = tuple(
        {"X": "off", "?": "possible", "O": "on"}[x] for x in data["b"]
    )
    certain = windows(codes, "off")
    possible = windows(codes, "possible")
    lines = [f"📅 {words[6]} · {words[7]} {group}"]
    lines.extend(f"🪫 {words[8]} {a}–{b}" for a, b in certain[:6])
    lines.extend(f"⚠️ {words[9]} {a}–{b}" for a, b in possible[:6])
    lines.append(f"{words[10]}: outage-data-ua")
    lines.append(words[11] + " (Shelly).")
    return lines


def widget_fallback_text(
    payload: str,
    now: float | None = None,
    language: str | None = None,
) -> str:
    """Build a readable fallback caption with no network or Telegram send."""

    try:
        data = json.loads(payload)
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid widget state") from exc
    if not isinstance(data, dict) or data.get("schema") != 1:
        raise ValueError("unsupported widget state schema")
    lang = language if language is not None else data.get("l", "uk")
    if lang not in WORDS:
        raise ValueError("unsupported caption language")
    clock = time.time() if now is None else now
    lines = ["⚡ Shelly"]
    lines.extend(measured_lines(data, clock, WORDS[lang]))
    lines.extend(forecast_lines(data, clock, WORDS[lang]))
    caption = "\n".join(lines)
    if len(caption) > 1024:
        raise ValueError("fallback caption exceeds Telegram limit")
    return caption


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", required=True, help="Widget JSON state")
    parser.add_argument("--language", choices=("ru", "uk", "en"))
    args = parser.parse_args()
    print(widget_fallback_text(args.state, language=args.language))
