"""Pure, opt-in low-voltage advisory state machine for future acceptance."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class WarningPolicy:
    """Bounded voltage warning thresholds with a disabled default."""

    enabled: bool = False
    below_v: float = 207.0
    clear_v: float = 210.0
    dwell_sec: int = 180
    clear_sec: int = 120
    max_sample_gap_sec: int = 45


@dataclass(frozen=True)
class WarningState:
    """Persistable in-memory advisory state, independent of Shelly writes."""

    active: bool = False
    low_since: float | None = None
    clear_since: float | None = None
    last_ts: float | None = None


def validate_policy(policy: WarningPolicy) -> None:
    """Reject unsafe, inverted or immediate-action warning policies."""

    bounds = (
        0 < policy.below_v < policy.clear_v < 500
        and math.isfinite(policy.below_v)
        and math.isfinite(policy.clear_v)
        and 30 <= policy.dwell_sec <= 3600
        and 30 <= policy.clear_sec <= 3600
        and 10 <= policy.max_sample_gap_sec <= 300
    )
    if not bounds:
        raise ValueError("invalid low-voltage warning policy")


def valid_sample(voltage: float | None) -> bool:
    """Reject offline placeholders and physically implausible voltages."""

    return (
        isinstance(voltage, (int, float))
        and not isinstance(voltage, bool)
        and math.isfinite(voltage)
        and 0 < voltage < 500
    )


def observe_warning(
    state: WarningState,
    voltage: float | None,
    timestamp: float,
    policy: WarningPolicy | None = None,
) -> tuple[WarningState, str | None]:
    """Report only sustained low voltage and confirmed sustained recovery."""

    settings = policy or WarningPolicy()
    validate_policy(settings)
    if not settings.enabled or not math.isfinite(timestamp):
        return state, None
    if state.last_ts is not None and timestamp <= state.last_ts:
        return state, None
    if (
        state.last_ts is not None
        and timestamp - state.last_ts > settings.max_sample_gap_sec
    ):
        state = replace(state, low_since=None, clear_since=None)
    if not valid_sample(voltage):
        return replace(
            state, last_ts=timestamp, low_since=None, clear_since=None
        ), None
    if state.active:
        return evaluate_recovery(state, voltage, timestamp, settings)
    return evaluate_low_voltage(state, voltage, timestamp, settings)


def evaluate_low_voltage(
    state: WarningState,
    voltage: float,
    now: float,
    policy: WarningPolicy,
) -> tuple[WarningState, str | None]:
    """Require continuous low readings before starting a warning."""

    if voltage >= policy.below_v:
        return replace(state, last_ts=now, low_since=None), None
    since = state.low_since if state.low_since is not None else now
    triggered = now - since >= policy.dwell_sec
    updated = replace(
        state,
        last_ts=now,
        low_since=None if triggered else since,
        active=triggered,
    )
    return updated, "warning_started" if triggered else None


def evaluate_recovery(
    state: WarningState,
    voltage: float,
    now: float,
    policy: WarningPolicy,
) -> tuple[WarningState, str | None]:
    """Clear an existing warning only after stable upper-threshold readings."""

    if voltage < policy.clear_v:
        return replace(state, last_ts=now, clear_since=None), None
    since = state.clear_since if state.clear_since is not None else now
    cleared = now - since >= policy.clear_sec
    updated = replace(
        state,
        last_ts=now,
        active=not cleared,
        clear_since=None if cleared else since,
    )
    return updated, "warning_cleared" if cleared else None


SETTINGS_PREFIX = "early_warning_"


def policy_to_settings(policy: WarningPolicy) -> dict[str, str]:
    """Serialize an advisory policy as plain, auditable setting values."""

    validate_policy(policy)
    return {
        f"{SETTINGS_PREFIX}enabled": "1" if policy.enabled else "0",
        f"{SETTINGS_PREFIX}below_v": f"{policy.below_v:g}",
        f"{SETTINGS_PREFIX}clear_v": f"{policy.clear_v:g}",
        f"{SETTINGS_PREFIX}dwell_sec": str(policy.dwell_sec),
        f"{SETTINGS_PREFIX}clear_sec": str(policy.clear_sec),
        f"{SETTINGS_PREFIX}max_gap_sec": str(policy.max_sample_gap_sec),
    }


def policy_from_settings(values: Mapping[str, str]) -> WarningPolicy:
    """Read strict opt-in settings without altering existing alert policy."""

    defaults = policy_to_settings(WarningPolicy())
    merged = {
        name: values.get(name, default) for name, default in defaults.items()
    }
    enabled = merged[f"{SETTINGS_PREFIX}enabled"]
    if enabled not in {"0", "1"}:
        raise ValueError("early-warning enabled flag must be 0 or 1")
    try:
        policy = WarningPolicy(
            enabled=enabled == "1",
            below_v=float(merged[f"{SETTINGS_PREFIX}below_v"]),
            clear_v=float(merged[f"{SETTINGS_PREFIX}clear_v"]),
            dwell_sec=int(merged[f"{SETTINGS_PREFIX}dwell_sec"]),
            clear_sec=int(merged[f"{SETTINGS_PREFIX}clear_sec"]),
            max_sample_gap_sec=int(merged[f"{SETTINGS_PREFIX}max_gap_sec"]),
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("invalid early-warning settings") from exc
    validate_policy(policy)
    return policy


def warning_collision_reason(
    policy: WarningPolicy,
    existing_low_v: float,
    existing_enabled: bool,
) -> str | None:
    """Detect intersecting voltage regions for independent notifications."""

    validate_policy(policy)
    if not math.isfinite(existing_low_v) or not 0 < existing_low_v < 500:
        raise ValueError("existing low alert threshold is invalid")
    if not policy.enabled or not existing_enabled:
        return None
    if policy.below_v <= existing_low_v:
        return "duplicate_low_voltage_range"
    return "broader_warning_requires_single_notification_flow"


def overlaps_existing_alert(
    policy: WarningPolicy,
    existing_low_v: float,
    existing_enabled: bool,
) -> bool:
    """Block independent warnings whenever enabled low ranges intersect."""

    return (
        warning_collision_reason(policy, existing_low_v, existing_enabled)
        is not None
    )


def validate_unique_notification_path(
    policy: WarningPolicy,
    existing_low_v: float,
    existing_enabled: bool,
) -> None:
    """Reject parallel warning delivery before any Telegram integration."""

    reason = warning_collision_reason(policy, existing_low_v, existing_enabled)
    if reason:
        raise ValueError(
            "independent early warning would overlap active level alert: "
            + reason
        )
