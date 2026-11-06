"""Read published DTEK schedules without controlling Shelly hardware."""

from __future__ import annotations

import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

REGIONS = {
    "k": "https://www.dtek-kem.com.ua/ua/shutdowns",
    "kr": "https://www.dtek-krem.com.ua/ua/shutdowns",
    "dn": "https://www.dtek-dnem.com.ua/ua/shutdowns",
    "o": "https://www.dtek-oem.com.ua/ua/shutdowns",
    "d": "https://www.dtek-dem.com.ua/ua/shutdowns",
}
STATE = {"yes": (True, True), "no": (False, False),
         "first": (False, True), "second": (True, False)}


def extract_schedule(html: str) -> dict:
    """Decode the DTEK 'fact' object without greedy regular expressions."""

    marker = re.search(r"\bfact\s*=\s*\{", html)
    if marker is None:
        raise ValueError("DTEK schedule data not found")
    data, _ = json.JSONDecoder().raw_decode(html[marker.end() - 1:])
    if not isinstance(data, dict):
        raise ValueError("DTEK schedule data is invalid")
    return data


def outage_periods(hours: dict) -> list[tuple[str, str]]:
    """Combine consecutive half-hour outages into readable intervals."""

    slots = []
    for hour in range(24):
        value = hours.get(str(hour + 1), hours.get(hour + 1))
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
    """Format a half-hour index as a local clock time."""

    return f"{slot // 2:02d}:{(slot % 2) * 30:02d}"


def get_schedule(region: str, group: str, session=None) -> tuple[str, list]:
    """Fetch public regional HTML and return the current-day intervals."""

    if region not in REGIONS or not re.fullmatch(r"\d{1,2}\.\d", group):
        raise ValueError("DTEK region or group is invalid")
    client = session or requests
    response = client.get(REGIONS[region], timeout=10)
    response.raise_for_status()
    data = extract_schedule(response.text)
    day = datetime.now(ZoneInfo("Europe/Kyiv")).strftime("%Y-%m-%d")
    schedule = data.get("data", {}).get(day)
    if schedule is None:
        schedule = data.get(day)
    if not isinstance(schedule, dict):
        raise ValueError("DTEK has no confirmed schedule for today")
    hours = schedule.get(f"GPV{group}")
    if not isinstance(hours, dict):
        raise ValueError("DTEK group was not found")
    return day, outage_periods(hours)


def format_schedule(language: str, region: str, group: str) -> str:
    """Build a read-only forecast with an explicit reliability warning."""

    if not region or not group:
        return "ДТЭК: укажите DTEK_REGION и DTEK_GROUP в настройках."
    try:
        day, periods = get_schedule(region, group)
    except (requests.RequestException, ValueError, json.JSONDecodeError):
        return "ДТЭК: график сейчас недоступен. Сведения об отключениях неизвестны."
    title = {"ru": "Плановые отключения", "uk": "Планові відключення",
             "en": "Scheduled outages"}.get(language, "Плановые отключения")
    lines = [f"📅 {title} ДТЭК · {day} · {group}"]
    lines.extend(f"🪫 {start}–{end}" for start, end in periods)
    if not periods:
        lines.append("✅ По опубликованному графику отключений нет")
    lines.append("ℹ️ Прогноз ДТЭК, не фактические показания Shelly.")
    return "\n".join(lines)
