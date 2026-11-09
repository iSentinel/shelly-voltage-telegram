"""Preview manually entered forecasts without verified-source claims."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

KYIV = ZoneInfo("Europe/Kyiv")
GROUP_RE = re.compile(r"\b(?:група|группа|group)\s*(\d{1,2}\.\d)\b", re.I)
DATE_RE = re.compile(r"\b(\d{2})\.(\d{2})(?:\.(\d{4}))?\b")
WINDOW_RE = re.compile(
    r"^\s*🪫\s*(\d{2}:\d{2})\s*[-–—]\s*"
    r"(\d{2}:\d{2})(?:\s*\([^\n]*\))?\s*$",
    re.M,
)
HEADER_RE = re.compile(
    r"(?m)^[ \t]*(?:🗓[ \t]*)?"
    r"(?:Прогнозований графік|Прогнозируемый график|Forecast)"
    r"\b[^\n]*$",
    re.I,
)
MANUAL_SOURCE = "user_supplied_unverified"
LABELS = {
    "ru": (
        "Ручной прогноз",
        "Источник: сообщение пользователя; "
        "ДТЭК не проверен. Фактическое питание неизвестно.",
    ),
    "uk": (
        "Прогноз, введений вручну",
        "Джерело: повідомлення користувача; "
        "ДТЕК не перевірено. Фактичне живлення невідоме.",
    ),
    "en": (
        "Manually entered forecast",
        "Source: user-provided message; "
        "DTEK not verified. Actual power status unknown.",
    ),
}


@dataclass(frozen=True)
class ManualForecast:
    """A user-reported schedule kept separate from verified DTEK data."""

    day: str
    group: str
    periods: tuple[tuple[str, str], ...]
    source: str = MANUAL_SOURCE
    provider_verified: bool = False


def local_stamp(day: date, clock: str) -> float:
    """Validate clock and reject fall DST ambiguity."""

    if clock == "24:00":
        return datetime.combine(
            day + timedelta(days=1), time(0), KYIV
        ).timestamp()
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):(?:00|30)", clock):
        raise ValueError("invalid scheduled half-hour")
    hour, minute = (int(part) for part in clock.split(":"))
    local = datetime.combine(day, time(hour, minute), KYIV)
    if local.utcoffset() != local.replace(fold=1).utcoffset():
        raise ValueError("ambiguous local DST time")
    return local.timestamp()


def validate_periods(
    day: date,
    periods: tuple[tuple[str, str], ...],
) -> None:
    """Accept only ordered non-overlapping half-hour forecast windows."""

    if not 1 <= len(periods) <= 24:
        raise ValueError("missing or excessive forecast windows")
    previous = None
    for start, end in periods:
        if start == "24:00":
            raise ValueError("24:00 cannot start a window")
        low, high = local_stamp(day, start), local_stamp(day, end)
        if low >= high or (previous is not None and low < previous):
            raise ValueError("out-of-order or overlapping forecast windows")
        previous = high


def parse_manual_forecast(text: str, day: date) -> ManualForecast:
    """Parse a pasted Ukrainian/Russian forecast with explicit date."""

    if not isinstance(text, str) or len(text) > 4096:
        raise ValueError("forecast message exceeds size limit")
    if len(HEADER_RE.findall(text)) > 1:
        raise ValueError("multiple daily forecasts need separate parsing")
    group = GROUP_RE.search(text)
    header = text.splitlines()[0] if text else ""
    match = DATE_RE.search(header)
    windows = tuple(WINDOW_RE.findall(text))
    lines = re.findall(r"^\s*🪫", text, re.M)
    if not group or not match or not windows:
        raise ValueError("forecast must include group, date and windows")
    if len(windows) != len(lines):
        raise ValueError("forecast contains malformed outage windows")
    stated_day = date(
        int(match[3]) if match[3] else day.year,
        int(match[2]),
        int(match[1]),
    )
    if stated_day != day:
        raise ValueError("forecast date differs from requested day")
    validate_periods(day, windows)
    return ManualForecast(day.isoformat(), group[1], windows)


def header_calendar_day(header: str, year: int) -> date:
    """Validate a date claimed in one manually relayed forecast header."""

    found = DATE_RE.search(header)
    if found is None:
        raise ValueError("forecast header has no date")
    stated_year = int(found[3]) if found[3] else year
    if stated_year != year:
        raise ValueError("forecast date is from the wrong year")
    return date(stated_year, int(found[2]), int(found[1]))


def parse_manual_forecast_days(
    text: str, year: int
) -> tuple[ManualForecast, ...]:
    """Separate up to two chronological daily forecast blocks."""

    if not isinstance(text, str) or len(text) > 8192:
        raise ValueError("forecast batch exceeds size limit")
    headers = list(HEADER_RE.finditer(text))
    if not 1 <= len(headers) <= 2:
        raise ValueError("expected one or two dated forecast headers")
    if re.search(r"(?m)^[ \t]*🪫", text[: headers[0].start()]):
        raise ValueError("orphaned outage window before first header")
    forecasts = []
    for index, match in enumerate(headers):
        day = header_calendar_day(match.group(), year)
        end = (
            headers[index + 1].start()
            if index + 1 < len(headers)
            else len(text)
        )
        section = text[match.start() : end].strip()
        forecast = parse_manual_forecast(section, day)
        if forecasts and forecast.group != forecasts[0].group:
            raise ValueError("mixed groups need separate forecast batches")
        if forecasts and day != date.fromisoformat(
            forecasts[-1].day
        ) + timedelta(days=1):
            raise ValueError("dates must be consecutive and ordered")
        forecasts.append(forecast)
    return tuple(forecasts)


def preview_manual_forecast(
    forecast: ManualForecast,
    language: str = "uk",
) -> str:
    """Render private text with an unverified-source disclaimer."""

    title, note = LABELS.get(language, LABELS["uk"])
    validate_periods(date.fromisoformat(forecast.day), forecast.periods)
    if forecast.source != MANUAL_SOURCE or forecast.provider_verified:
        raise ValueError("unexpected manual forecast source metadata")
    lines = [f"📅 {title} · {forecast.day} · {forecast.group}"]
    lines.extend(f"🪫 {begin}–{end}" for begin, end in forecast.periods)
    lines.append(f"ℹ️ {note}")
    return "\n".join(lines)
