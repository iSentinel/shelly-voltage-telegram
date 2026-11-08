"""Build a compact, stale-safe Telegram widget state without network calls."""

from __future__ import annotations

import json
import math
import re
import time
from datetime import date, datetime, timedelta, timezone
from datetime import time as dt_time
from zoneinfo import ZoneInfo

KYIV = ZoneInfo("Europe/Kyiv")
PUBLIC_DATA_LIMIT = 1024
STATE_VERSION = 1
MAX_SAMPLE_AGE = 60
MAX_FORECAST_AGE = 2 * 3600
BARS = frozenset("OX?")


def numeric(value: object, low: float, high: float) -> float | None:
    """Return a bounded finite number, never silently accepting booleans."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or not low <= number <= high:
        return None
    return number


def health_state(health: object, now: float) -> dict:
    """Expose current voltage only from a recently connected meter."""

    if not isinstance(health, dict):
        return {"s": "unknown"}
    connectivity = health.get("connectivity")
    if connectivity != "online":
        return {"s": "offline" if connectivity == "offline" else "unknown"}
    ts = numeric(health.get("last_success"), 1, now + 2)
    if ts is None or not 0 <= now - ts <= MAX_SAMPLE_AGE:
        return {"s": "stale"}
    voltage = numeric(health.get("voltage"), 0, 400)
    if voltage is None:
        return {"s": "unknown"}
    result = {"s": "online", "v": round(voltage, 2), "t": int(ts)}
    frequency = numeric(health.get("frequency"), 40, 70)
    if frequency is not None:
        result["f"] = round(frequency, 2)
    return result


def forecast_state(
    bars: str | None, day: str | None, updated: float | None, now: float
) -> dict:
    """Include a distinct community forecast only when verified fresh."""

    if bars is None:
        return {}
    if not isinstance(bars, str) or len(bars) != 48:
        raise ValueError("forecast must have 48 half-hour slots")
    if any(char not in BARS for char in bars):
        raise ValueError("forecast contains unknown slot states")
    if not isinstance(day, str):
        raise ValueError("forecast day is missing")
    expected = datetime.fromtimestamp(now, KYIV).date().isoformat()
    if day != expected:
        raise ValueError("community forecast day differs from today")
    fresh = numeric(updated, 1, now + 2)
    if fresh is None or not 0 <= now - fresh <= MAX_FORECAST_AGE:
        raise ValueError("community forecast is stale")
    next_day = date.fromisoformat(day) + timedelta(days=1)
    expires = datetime.combine(next_day, dt_time.min, KYIV).timestamp()
    return {
        "b": bars,
        "d": day,
        "u": int(fresh),
        "e": round(expires),
        "src": "community",
    }


def widget_public_data(
    health: dict,
    group: str = "",
    bars: str | None = None,
    day: str | None = None,
    forecast_updated: float | None = None,
    now: float | None = None,
    language: str = "uk",
    phases: dict[str, dict] | None = None,
) -> str:
    """Encode an opaque per-widget state fitting Bot API widget limits."""

    if language not in ("ru", "uk", "en"):
        raise ValueError("unsupported widget language")
    clock = now if now is not None else time.time()
    state = {
        "schema": STATE_VERSION,
        "l": language,
        **health_state(health, clock),
    }
    if phases is not None:
        if not isinstance(phases, dict) or set(phases) != {"L2", "L3"}:
            raise ValueError("three-phase widget needs L2 and L3")
        if not all(isinstance(phases[name], dict) for name in phases):
            raise ValueError("invalid secondary phase snapshot")
        state["p"] = {
            name: health_state(phases[name], clock) for name in ("L2", "L3")
        }
    if group:
        if not re.fullmatch(r"\d{1,2}\.\d", group):
            raise ValueError("unsupported outage group")
        state["g"] = group
    state.update(forecast_state(bars, day, forecast_updated, clock))
    encoded = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > PUBLIC_DATA_LIMIT:
        raise ValueError("widget state exceeds Telegram size limit")
    return encoded


def slots_from_community(source: object, region: str, group: str, now=None):
    """Reuse validated community parsing to construct a forecast bar."""

    from outage_data_ua import current_groups, parse_snapshot, slots_for

    timestamp = now or datetime.now(timezone.utc)
    valid = parse_snapshot(source, region, group, timestamp)
    groups = current_groups(source["fact"], timestamp)
    slot_states = slots_for(groups[f"GPV{group}"])
    alphabet = {"on": "O", "off": "X", "possible": "?"}
    bars = "".join(alphabet[slot] for slot in slot_states)
    return bars, valid.day, valid.source_updated.timestamp()
