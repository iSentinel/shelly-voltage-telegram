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


MESSAGES = {
    "ru": (
        "ДТЭК: укажите DTEK_REGION и DTEK_GROUP в настройках.",
        "ДТЭК: график недоступен. Сведения об отключениях неизвестны.",
        "Плановые отключения ДТЭК",
        "✅ По опубликованному графику отключений нет",
        "ℹ️ Прогноз ДТЭК, не фактические показания Shelly.",
    ),
    "uk": (
        "ДТЕК: укажіть DTEK_REGION і DTEK_GROUP у налаштуваннях.",
        "ДТЕК: графік недоступний. Інформація про відключення невідома.",
        "Планові відключення ДТЕК",
        "✅ За оприлюдненим графіком відключень немає",
        "ℹ️ Прогноз ДТЕК, а не фактичні показники Shelly.",
    ),
    "en": (
        "DTEK: set DTEK_REGION and DTEK_GROUP in settings.",
        "DTEK: schedule unavailable. Outage information is unknown.",
        "DTEK scheduled outages",
        "✅ No outages in the published schedule",
        "ℹ️ DTEK forecast, not live measurements from Shelly.",
    ),
}


def extract_schedule(html: str) -> dict:
    """Decode the DTEK fact object with bounded input and JSON parsing."""

    if len(html) > 2_000_000:
        raise ValueError("DTEK source HTML exceeds size limit")
    if "_Incapsula_Resource" in html or "incapsula" in html.lower():
        raise ValueError("DTEK anti-bot challenge; no schedule available")
    marker = re.search(r"\bfact\s*=\s*\{", html)
    if marker is None:
        raise ValueError("DTEK schedule data not found")
    data, _ = json.JSONDecoder().raw_decode(html[marker.end() - 1 :])
    if not isinstance(data, dict):
        raise ValueError("DTEK schedule data is invalid")
    return data


def parse_source_day(value: object):
    """Interpret a DTEK day label or an exact Kyiv-midnight Unix second."""

    if isinstance(value, bool):
        raise ValueError("DTEK source date is invalid")
    if isinstance(value, int) or (
        isinstance(value, str) and re.fullmatch(r"\d{10}", value)
    ):
        stamp = int(value)
        if not 1_000_000_000 <= stamp <= 4_102_444_800:
            raise ValueError("DTEK Unix source date is out of range")
        try:
            local = datetime.fromtimestamp(stamp, ZoneInfo("Europe/Kyiv"))
        except (ValueError, OSError, OverflowError) as exc:
            raise ValueError("DTEK source date is invalid") from exc
        if local.hour or local.minute or local.second:
            raise ValueError("DTEK timestamp is not Kyiv local midnight")
        return local.date()
    if not isinstance(value, str):
        raise ValueError("DTEK source date is invalid")
    for pattern in ("%Y-%m-%d", "%d.%m.%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(value, pattern).date()
        except ValueError:
            continue
    raise ValueError("DTEK source date has unknown format")


def published_groups(fact: dict, now: datetime) -> dict:
    """Accept only one exact local-day record, including Unix day keys."""

    if now.tzinfo is None or not isinstance(fact, dict):
        raise ValueError("DTEK source date requires timezone and fact")
    today = now.astimezone(ZoneInfo("Europe/Kyiv")).date()
    source_day = fact.get("today")
    if source_day is not None and parse_source_day(source_day) != today:
        raise ValueError("DTEK source date differs from today")
    days = fact.get("data", fact)
    if not isinstance(days, dict):
        raise ValueError("DTEK days missing")
    matches = []
    for day, groups in days.items():
        try:
            local_day = parse_source_day(day)
        except ValueError:
            continue
        if local_day == today:
            if not isinstance(groups, dict) or not groups:
                raise ValueError("DTEK schedule groups are missing")
            matches.append(groups)
    if len(matches) != 1:
        raise ValueError("DTEK schedule for today missing or ambiguous")
    return matches[0]


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
    """Format a localized forecast, preserving unknown provider state."""

    missing, unavailable, title, empty, disclaimer = MESSAGES.get(
        language, MESSAGES["ru"]
    )
    if not region or not group:
        return missing
    try:
        day, periods = get_schedule(region, group)
    except (ValueError, OSError, UnicodeError, json.JSONDecodeError):
        return unavailable
    lines = [f"📅 {title} · {day} · {group}"]
    lines.extend(f"🪫 {start}–{end}" for start, end in periods)
    if not periods:
        lines.append(empty)
    lines.append(disclaimer)
    return "\n".join(lines)
