"""Conservatively classify DTEK address-level outage API responses."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


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
    return ProviderEvent(category, reason or kind, start, end,
                         clean_field(update, 80))


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

    fields = [event.category, event.reason, event.started,
              event.expected_end]
    encoded = json.dumps(fields, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:20]
