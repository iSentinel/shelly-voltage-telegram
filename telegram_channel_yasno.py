"""Append a source-labelled YASNO forecast to standard Telegram status text."""

from __future__ import annotations

from datetime import datetime, timezone

from telegram_schedule_periods import format_schedule_rows
from yasno_cache import read_verified_cache
from yasno_schedule import clock_for_slot, parse_schedule

MESSAGES = {
    "uk": (
        "група",
        "Планові відключення",
        "Можливі відключення",
        "Не заплановано відключень",
        "Графік недоступний або застарів",
        "Прогноз, а не показання електроживлення",
    ),
    "ru": (
        "группа",
        "Плановые отключения",
        "Возможные отключения",
        "Плановых отключений нет",
        "График недоступен или устарел",
        "Прогноз, а не измерение электроснабжения",
    ),
    "en": (
        "group",
        "Planned outages",
        "Possible outages",
        "No planned outages",
        "Schedule unavailable or expired",
        "Forecast, not measured mains power",
    ),
}


def time_windows(bars: str, marker: str) -> list[str]:
    """Convert exact half-hour markers into time windows."""

    result = []
    begin = None
    for index in range(len(bars) + 1):
        matched = index < len(bars) and bars[index] == marker
        if matched and begin is None:
            begin = index
        elif not matched and begin is not None:
            result.append(f"{clock_for_slot(begin)}–{clock_for_slot(index)}")
            begin = None
    return result


def join_windows(values: list[str]) -> str:
    """Bound the number of ranges in a Telegram status message."""

    listed = ", ".join(values[:6])
    overflow = len(values) - 6
    return f"{listed} (+{overflow})" if overflow > 0 else listed


def forecast_section(group: str, bars: str, language: str) -> str:
    """Render chronological off/on windows with durations and source label."""

    words = MESSAGES.get(language, MESSAGES["uk"])
    certain = time_windows(bars, "X")
    possible = time_windows(bars, "?")
    lines = [f"🗓 YASNO · ДТЕК Київ | {words[0]} {group}"]
    if certain:
        lines.append(f"🪫 {words[1]}:")
    if possible:
        lines.append(f"⚠️ {words[2]}:")
    if not certain and not possible:
        lines.append(f"ℹ️ {words[3]}")
    intervals = format_schedule_rows(bars, language)
    lines.extend(intervals[:8])
    if len(intervals) > 8:
        lines.append(f"… (+{len(intervals) - 8})")
    lines.append(f"ℹ️ {words[5]}")
    return "\n".join(lines)


def append_yasno_forecast(
    status: str,
    path: str,
    group: str,
    language: str,
    now: datetime | None = None,
) -> str:
    """Use only a verified local cache, never any Telegram or HTTP calls."""

    clock = now or datetime.now(timezone.utc)
    try:
        payload, timestamp = read_verified_cache(path)
        result = parse_schedule(payload, group, clock, timestamp)
        section = forecast_section(result.group, result.bars, language)
    except (ValueError, OSError, KeyError, TypeError):
        section = f"🗓 YASNO: {MESSAGES.get(language, MESSAGES['uk'])[4]}"
    combined = status + "\n\n" + section
    # Telegram limits text to 4096 characters. UTF-16 is conservative.
    if len(combined.encode("utf-16-le")) // 2 > 4096:
        return status
    return combined
