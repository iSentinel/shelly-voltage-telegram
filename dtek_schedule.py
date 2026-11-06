"""Read independently collected DTEK schedules without touching Shelly."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

REGIONS = {
    "k": "https://www.dtek-kem.com.ua/ua/shutdowns",
    "kr": "https://www.dtek-krem.com.ua/ua/shutdowns",
    "dn": "https://www.dtek-dnem.com.ua/ua/shutdowns",
    "o": "https://www.dtek-oem.com.ua/ua/shutdowns",
    "d": "https://www.dtek-dem.com.ua/ua/shutdowns",
}
STATE = {
    "yes": (True, True),
    "no": (False, False),
    "first": (False, True),
    "second": (True, False),
}
CACHE_MAX_BYTES = 512 * 1024
CACHE_MAX_AGE = timedelta(hours=2)


def extract_schedule(html: str) -> dict:
    """Decode the DTEK fact object with bounded input and JSON parsing."""

    if len(html) > 2_000_000:
        raise ValueError("DTEK source HTML exceeds size limit")
    marker = re.search(r"\bfact\s*=\s*\{", html)
    if marker is None:
        raise ValueError("DTEK schedule data not found")
    data, _ = json.JSONDecoder().raw_decode(html[marker.end() - 1 :])
    if not isinstance(data, dict):
        raise ValueError("DTEK schedule data is invalid")
    return data


def published_groups(fact: dict, now: datetime) -> dict:
    """Return only today's published groups, rejecting a wrong source date."""

    today = now.astimezone(ZoneInfo("Europe/Kyiv")).date()
    allowed = (
        today.isoformat(),
        today.strftime("%d.%m.%Y"),
        today.strftime("%d-%m-%Y"),
    )
    source_day = fact.get("today")
    if source_day is not None and source_day not in allowed:
        raise ValueError("DTEK source date differs from today")
    days = fact.get("data", fact)
    if not isinstance(days, dict):
        raise ValueError("DTEK days missing")
    for day in allowed:
        groups = days.get(day)
        if isinstance(groups, dict) and groups:
            return groups
    raise ValueError("DTEK schedule for today missing")


def outage_periods(hours: dict) -> list[tuple[str, str]]:
    """Merge consecutive half-hour outages and require all 24 hours."""

    slots = []
    for hour in range(1, 25):
        value = hours.get(str(hour), hours.get(hour))
        if value not in STATE:
            raise ValueError("DTEK schedule is incomplete")
        slots.extend(STATE[value])
    result = []
    start = None
    for slot, on in enumerate(slots + [True]):
        if not on and start is None:
            start = slot
        if on and start is not None:
            result.append((clock(start), clock(slot)))
            start = None
    return result


def clock(slot: int) -> str:
    """Format a half-hour slot as local time."""

    return f"{slot // 2:02d}:{(slot % 2) * 30:02d}"


def validate_cache(data: dict, region: str, now: datetime) -> dict:
    """Reject stale, malformed or wrong-region collected schedules."""

    if not isinstance(data, dict):
        raise ValueError("DTEK cache root invalid")
    if data.get("version") != 1 or data.get("region") != region:
        raise ValueError("DTEK cache metadata invalid")
    stamp = datetime.fromisoformat(str(data.get("fetched_at", "")))
    if stamp.tzinfo is None:
        raise ValueError("DTEK cache timestamp has no timezone")
    age = now - stamp.astimezone(timezone.utc)
    if age > CACHE_MAX_AGE or age < -timedelta(minutes=2):
        raise ValueError("DTEK cache stale or from future")
    fact = data.get("fact")
    if not isinstance(fact, dict):
        raise ValueError("DTEK cache has no fact object")
    published_groups(fact, now)
    return fact


def get_schedule(
    region: str,
    group: str,
    cache_path: str | Path | None = None,
    now: datetime | None = None,
) -> tuple[str, list[tuple[str, str]]]:
    """Read an optional local snapshot, with no network or device calls."""

    if region not in REGIONS or not re.fullmatch(r"\d{1,2}\.\d", group):
        raise ValueError("DTEK region or group is invalid")
    filename = cache_path or os.getenv("DTEK_CACHE_FILE")
    path = Path(filename or "/data/dtek_schedule.json")
    if path.is_symlink():
        raise ValueError("DTEK cache symlinks forbidden")
    if path.stat().st_size > CACHE_MAX_BYTES:
        raise ValueError("DTEK cache too large")
    data = json.loads(path.read_text(encoding="utf-8"))
    current = now or datetime.now(timezone.utc)
    fact = validate_cache(data, region, current)
    hours = published_groups(fact, current).get(f"GPV{group}")
    if not isinstance(hours, dict):
        raise ValueError("DTEK group not found")
    day = current.astimezone(ZoneInfo("Europe/Kyiv")).date().isoformat()
    return day, outage_periods(hours)


def format_schedule(language: str, region: str, group: str) -> str:
    """Format a forecast, preserving unknown state when source is absent."""

    if not region or not group:
        return "ДТЭК: укажите DTEK_REGION и DTEK_GROUP в настройках."
    try:
        day, periods = get_schedule(region, group)
    except (ValueError, OSError, UnicodeError, json.JSONDecodeError):
        return "ДТЭК: график недоступен. Сведения об отключениях неизвестны."
    titles = {
        "ru": "Плановые отключения",
        "uk": "Планові відключення",
        "en": "Scheduled outages",
    }
    lines = [f"📅 {titles.get(language, titles['ru'])} ДТЭК · {day} · {group}"]
    lines.extend(f"🪫 {start}–{end}" for start, end in periods)
    if not periods:
        lines.append("✅ По опубликованному графику отключений нет")
    lines.append("ℹ️ Прогноз ДТЭК, не фактические показания Shelly.")
    return "\n".join(lines)
