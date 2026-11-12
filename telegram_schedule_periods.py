"""Build chronological half-hour YASNO intervals with exact durations."""

from __future__ import annotations

from yasno_schedule import clock_for_slot

SCHEDULE_MARKERS = frozenset("OX?")
STATUS_ICONS = {"X": "🪫", "O": "🔋", "?": "⚠️"}
HOUR_UNITS = {"uk": "год.", "ru": "ч.", "en": "h"}


def schedule_periods(bars: str) -> list[tuple[str, str, str, int]]:
    """Merge adjacent equal half-hour slots without losing 24:00."""

    if not isinstance(bars, str) or len(bars) != 48:
        raise ValueError("schedule needs 48 half-hour slots")
    if set(bars) - SCHEDULE_MARKERS:
        raise ValueError("schedule has invalid symbols")
    begin = 0
    periods = []
    for end in range(1, 49):
        if end == 48 or bars[end] != bars[begin]:
            periods.append(
                (
                    bars[begin],
                    clock_for_slot(begin),
                    clock_for_slot(end),
                    30 * (end - begin),
                )
            )
            begin = end
    return periods


def duration_label(minutes: int, language: str = "uk") -> str:
    """Express exact whole and half hours without rounding up."""

    if type(minutes) is not int or not (0 < minutes <= 1440):
        raise ValueError("duration is outside one calendar day")
    hours = f"{minutes // 60}"
    if minutes % 60:
        hours += ".5"
    return f"≈{hours} {HOUR_UNITS.get(language, HOUR_UNITS['uk'])}"


def format_schedule_rows(bars: str, language: str = "uk") -> list[str]:
    """Print battery-prefixed chronological planned electricity periods."""

    return [
        f"{STATUS_ICONS[kind]}{begin} - {end} "
        f"({duration_label(minutes, language)})"
        for kind, begin, end, minutes in schedule_periods(bars)
    ]
