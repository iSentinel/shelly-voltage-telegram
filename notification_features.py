"""Persistent voltage episode and instability helpers."""

from __future__ import annotations

import json

ABNORMAL_KINDS = {
    "low",
    "warning_low",
    "critical_low",
    "warning_high",
    "critical_high",
}


def load_json_object(raw: str | None) -> dict | None:
    """Load a stored JSON object or return None for invalid data."""

    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def transition_state(kind: str) -> str:
    """Reduce event detail to low, high, or normal for stability tracking."""

    if kind.endswith("_high"):
        return "high"
    if kind.endswith("_low") or kind == "low":
        return "low"
    return "normal"


def new_episode(kind: str, voltage: float, at: float, phase: str) -> dict:
    """Create one persistent voltage episode."""

    return {
        "start_at": at,
        "last_at": at,
        "phase": phase,
        "transitions": [{"at": at, "kind": kind, "voltage": voltage}],
        "finalized_at": None,
    }


def update_episode(
    episode: dict | None,
    kind: str,
    voltage: float,
    at: float,
    phase: str,
    window_seconds: float,
) -> dict:
    """Append a transition or start a new fixed-deadline episode."""

    if not episode or at - float(episode.get("start_at", 0)) > window_seconds:
        return new_episode(kind, voltage, at, phase)
    transitions = list(episode.get("transitions") or [])
    current = transition_state(kind)
    previous = (
        transition_state(str(transitions[-1]["kind"])) if transitions else None
    )
    if current != previous:
        transitions.append({"at": at, "kind": kind, "voltage": voltage})
    episode = dict(episode)
    episode.update(last_at=at, transitions=transitions, finalized_at=None)
    return episode


def abnormal_seconds(episode: dict, end_at: float) -> float:
    """Calculate time spent outside normal from confirmed transitions."""

    rows = list(episode.get("transitions") or [])
    total = 0.0
    for index, row in enumerate(rows):
        start = float(row["at"])
        stop = (
            end_at if index + 1 == len(rows) else float(rows[index + 1]["at"])
        )
        if str(row["kind"]) in ABNORMAL_KINDS:
            total += max(0.0, stop - start)
    return total


def load_history(raw: str | None) -> list[dict]:
    """Load a persisted compact transition history."""

    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return value if isinstance(value, list) else []


def update_history(
    history: list[dict],
    kind: str,
    phase: str,
    at: float,
    keep_seconds: float = 3600,
) -> list[dict]:
    """Append a state change and keep only recent deduplicated transitions."""

    state = transition_state(kind)
    fresh = [
        row for row in history if at - float(row.get("at", 0)) <= keep_seconds
    ]
    if (
        fresh
        and fresh[-1].get("state") == state
        and fresh[-1].get("phase") == phase
    ):
        return fresh
    fresh.append({"at": at, "state": state, "phase": phase})
    return fresh[-100:]


def instability_count(
    history: list[dict], now: float, seconds: float = 900
) -> int:
    """Count confirmed state transitions inside the instability window."""

    return sum(
        1 for row in history if 0 <= now - float(row.get("at", 0)) <= seconds
    )
