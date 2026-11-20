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
MIN_VISIBLE_VOLTAGE = 0.1  # A 0.0 V display is not a live supply reading.
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


def health_state(
    health: object, now: float, max_age: float = MAX_SAMPLE_AGE
) -> dict:
    """Expose current voltage only from a recently connected meter."""

    if not isinstance(health, dict):
        return {"s": "unknown"}
    connectivity = health.get("connectivity")
    if connectivity != "online":
        return {"s": "offline" if connectivity == "offline" else "unknown"}
    ts = numeric(health.get("last_success"), 1, now + 2)
    if ts is None or not 0 <= now - ts <= min(max_age, MAX_SAMPLE_AGE):
        return {"s": "stale"}
    voltage = numeric(health.get("voltage"), MIN_VISIBLE_VOLTAGE, 400)
    if voltage is None:
        return {"s": "unknown"}
    result = {"s": "online", "v": round(voltage, 2), "t": int(ts)}
    frequency = numeric(health.get("frequency"), 40, 70)
    if frequency is not None:
        result["f"] = round(frequency, 2)
    return result


def forecast_metadata(
    day: str | None, updated: float | None, now: float, source: str
) -> dict:
    """Return bounded provider timing, never inventing a schedule."""
    if source not in ("community", "yasno"):
        raise ValueError("unknown forecast source")
    if not isinstance(day, str):
        raise ValueError("forecast day is missing")
    expected = datetime.fromtimestamp(now, KYIV).date().isoformat()
    if day != expected:
        raise ValueError("forecast day differs from today")
    fresh = numeric(updated, 1, now + 2)
    if fresh is None or not 0 <= now - fresh <= MAX_FORECAST_AGE:
        raise ValueError("forecast is stale")
    next_day = date.fromisoformat(day) + timedelta(days=1)
    expires = datetime.combine(next_day, dt_time.min, KYIV).timestamp()
    return {"d": day, "u": int(fresh), "e": round(expires), "src": source}


def local_day_start(day: str) -> int:
    """Give thin clients a Kyiv midnight epoch without timezone libraries."""
    return int(
        datetime.combine(
            date.fromisoformat(day), dt_time.min, KYIV
        ).timestamp()
    )


def forecast_state(
    bars: str | None,
    day: str | None,
    updated: float | None,
    now: float,
    source: str = "community",
    notice: str | None = None,
) -> dict:
    """Encode either a fresh plan or an explicit official emergency notice."""
    if notice is not None:
        if notice != "emergency" or source != "yasno" or bars is not None:
            raise ValueError("invalid or conflicting forecast notice")
        meta = forecast_metadata(day, updated, now, source)
        return {"n": notice, "a": local_day_start(meta["d"]), **meta}
    if bars is None:
        return {}
    if not isinstance(bars, str) or len(bars) != 48:
        raise ValueError("forecast must have 48 half-hour slots")
    if any(char not in BARS for char in bars):
        raise ValueError("forecast contains unknown slot states")
    return {"b": bars, **forecast_metadata(day, updated, now, source)}


def valid_emergency_notice(state: dict, now: float) -> bool:
    """Only show explicit, unexpired official cancellation metadata."""
    if (
        state.get("n") != "emergency"
        or state.get("src") != "yasno"
        or "b" in state
        or not re.fullmatch(r"\d{1,2}\.\d", str(state.get("g", "")))
    ):
        return False
    try:
        meta = forecast_metadata(state.get("d"), state.get("u"), now, "yasno")
    except (ValueError, TypeError, OverflowError):
        return False
    return state.get("e") == meta["e"] and state.get("a") == local_day_start(
        meta["d"]
    )


def widget_phase_states(
    phases: dict[str, dict] | None, clock: float, max_age: float
) -> dict:
    """Encode secondary measurements only for the three-phase topology."""

    if phases is None:
        return {}
    if not isinstance(phases, dict) or set(phases) != {"L2", "L3"}:
        raise ValueError("three-phase widget needs L2 and L3")
    if not all(isinstance(snapshot, dict) for snapshot in phases.values()):
        raise ValueError("invalid secondary phase snapshot")
    return {
        name: health_state(snap, clock, max_age)
        for name, snap in phases.items()
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
    forecast_source: str = "community",
    max_sample_age: float = MAX_SAMPLE_AGE,
    voltage_limits: tuple[float, float, float, float] | None = None,
    forecast_notice: str | None = None,
) -> str:
    """Encode an opaque per-widget state fitting Bot API widget limits."""

    if language not in ("ru", "uk", "en"):
        raise ValueError("unsupported widget language")
    clock = now if now is not None else time.time()
    state = {
        "schema": STATE_VERSION,
        "l": language,
        **health_state(health, clock, max_sample_age),
    }
    secondary = widget_phase_states(phases, clock, max_sample_age)
    if secondary:
        state["p"] = secondary
    forecast = forecast_state(
        bars, day, forecast_updated, clock, forecast_source, forecast_notice
    )
    return encode_widget_state(state, group, voltage_limits, forecast)


def encode_widget_state(
    state: dict, group: str, voltage_limits: tuple | None, forecast: dict
) -> str:
    """Attach validated configuration and enforce the public state size."""
    if voltage_limits is not None:
        state["limits"] = voltage_limits
    if group:
        if not re.fullmatch(r"\d{1,2}\.\d", group):
            raise ValueError("unsupported outage group")
        state["g"] = group
    state.update(forecast)
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
