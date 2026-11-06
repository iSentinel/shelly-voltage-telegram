"""Conservatively classify DTEK address-level outage API responses."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from html import escape


@dataclass(frozen=True)
class ProviderEvent:
    """An external advisory, never a Shelly power-state measurement."""

    category: str
    reason: str = ""
    started: str = ""
    expected_end: str = ""
    source_updated: str = ""


def clean_field(value: object, limit: int = 160) -> str:
    """Normalize untrusted website text without storing an address."""

    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


def classify_house(house: object, update: object = "") -> ProviderEvent:
    """Treat incomplete and ambiguous DTEK house data as unknown."""

    if not isinstance(house, dict):
        return ProviderEvent("unknown")
    keys = ("sub_type", "start_date", "end_date", "type")
    if any(key not in house for key in keys):
        return ProviderEvent("unknown")
    if any(not isinstance(house[key], str) for key in keys):
        return ProviderEvent("unknown")
    reason = clean_field(house["sub_type"])
    kind = clean_field(house["type"])
    start = clean_field(house["start_date"], 64)
    end = clean_field(house["end_date"], 64)
    text = f"{kind} {reason}".casefold()
    if "авар" in text or "екстр" in text or "emergency" in text:
        category = "emergency"
    elif any(x in text for x in ("план", "граф", "стабіліз", "scheduled")):
        category = "planned"
    elif not any((reason, kind, start, end)):
        category = "none"
    else:
        category = "unknown"
    updated = clean_field(update, 80)
    return ProviderEvent(category, reason or kind, start, end, updated)


def parse_house_response(payload: object, house: str) -> ProviderEvent:
    """Return an advisory only for the specifically requested house."""

    if not isinstance(payload, dict) or not house:
        return ProviderEvent("unknown")
    data = payload.get("data")
    if not isinstance(data, dict):
        return ProviderEvent("unknown")
    return classify_house(data.get(house), payload.get("updateTimestamp"))


def event_key(event: ProviderEvent) -> str:
    """Deduplicate substantive event changes, not poll timestamps."""

    fields = [event.category, event.reason, event.started, event.expected_end]
    encoded = json.dumps(fields, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:20]


TITLES = {
    "ru": {
        "emergency": "⚠️ ДТЭК сообщает об аварийных работах",
        "planned": "📅 ДТЭК сообщает о плановых работах",
        "none": "ℹ️ ДТЭК не сообщает об активном отключении",
        "unknown": "❔ ДТЭК: информация об отключениях неизвестна",
    },
    "uk": {
        "emergency": "⚠️ ДТЕК повідомляє про аварійні роботи",
        "planned": "📅 ДТЕК повідомляє про планові роботи",
        "none": "ℹ️ ДТЕК не повідомляє про активне відключення",
        "unknown": "❔ ДТЕК: інформація про відключення невідома",
    },
    "en": {
        "emergency": "⚠️ DTEK reports emergency works",
        "planned": "📅 DTEK reports planned works",
        "none": "ℹ️ DTEK reports no active outage",
        "unknown": "❔ DTEK outage information is unknown",
    },
}
LABELS = {
    "ru": (
        "Причина",
        "Начало по ДТЭК",
        "Прогноз ДТЭК",
        "Обновление ДТЭК",
        "Источник: ДТЭК. Это не показания Shelly.",
    ),
    "uk": (
        "Причина",
        "Початок за ДТЕК",
        "Прогноз ДТЕК",
        "Оновлення ДТЕК",
        "Джерело: ДТЕК. Це не показники Shelly.",
    ),
    "en": (
        "Reason",
        "DTEK reported start",
        "DTEK estimate",
        "DTEK source update",
        "Source: DTEK. Not a Shelly measurement.",
    ),
}


def format_advisory(event: ProviderEvent, language: str = "uk") -> str:
    """Render a Telegram HTML preview without claiming actual power loss."""

    titles = TITLES.get(language, TITLES["uk"])
    reason, started, expected, updated, disclaimer = LABELS.get(
        language, LABELS["uk"]
    )
    category = event.category if event.category in titles else "unknown"
    lines = [f"<b>{titles[category]}</b>"]
    for label, value in (
        (reason, event.reason),
        (started, event.started),
        (expected, event.expected_end),
        (updated, event.source_updated),
    ):
        safe = escape(clean_field(value), quote=False)
        if safe and category not in {"unknown", "none"}:
            lines.append(f"{label}: {safe}")
    lines.append("")
    lines.append(f"<i>{disclaimer}</i>")
    return "\n".join(lines)
