"""Audit a closed Shelly incident's real 15-minute telemetry, read-only."""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from urllib.parse import quote

WINDOW_SECONDS = 900
MAX_SAMPLE_GAP_SECONDS = 90
MAX_LATEST_AGE_SECONDS = 45
MAX_PHASE_EVENT_SKEW_SECONDS = 5
PHASES = ("L1", "L2", "L3")


def open_readonly(path: Path) -> sqlite3.Connection:
    """Forbid links and open existing SQLite without write privileges."""

    if path.is_symlink() or not path.is_file():
        raise ValueError("database missing or linked")
    uri = "file:" + quote(str(path.resolve())) + "?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=5)


def blocked(reason: str, incident_id: int | None) -> dict:
    """Never confuse telemetry-only evidence with a full release gate."""

    return {
        "status": "blocked",
        "reason": reason,
        "incident_id": incident_id,
        "scope": "telemetry_only",
        "telegram_delivery_verified": False,
        "release_authorized": False,
    }


def valid_sample(row: tuple) -> bool:
    """Check physical plausibility of one measured voltage/frequency."""

    try:
        stamp, voltage, frequency = (float(value) for value in row)
        return (
            all(math.isfinite(value) for value in (stamp, voltage, frequency))
            and 0 < voltage < 500
            and 40 <= frequency <= 70
        )
    except (TypeError, ValueError, OverflowError):
        return False


def sample_quality(rows: list[tuple], ended: float) -> tuple[int, float]:
    """Reject malformed observations and any gap over 90 seconds."""

    stamps = [ended]
    for stamp, voltage, frequency in rows:
        if not valid_sample((stamp, voltage, frequency)):
            raise ValueError("invalid voltage or frequency")
        if float(stamp) < stamps[-1]:
            raise ValueError("nonmonotonic recovery timestamps")
        stamps.append(float(stamp))
    stamps.append(ended + WINDOW_SECONDS)
    gap = max(b - a for a, b in zip(stamps, stamps[1:]))
    if not rows or gap > MAX_SAMPLE_GAP_SECONDS:
        raise ValueError("incomplete 15-minute telemetry window")
    return len(rows), round(gap, 2)


def closed_event(
    db: sqlite3.Connection, incident_id: int, now: float
) -> tuple[float, str]:
    """Require one real latest incident closed at a sensible timestamp."""

    incident = db.execute(
        "SELECT id,started_at,ended_at,phase FROM outages "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if incident is None or incident[0] != incident_id:
        raise ValueError("incident is not the latest event")
    _id, started, ended, phase = incident
    if ended is None:
        raise ValueError("incident still open")
    if not (math.isfinite(ended) and started < ended <= now):
        raise ValueError("incident closure timestamp invalid")
    if now - ended < WINDOW_SECONDS:
        raise ValueError("recovery window is not complete")
    return ended, phase


def inspect_samples(
    db: sqlite3.Connection, ended: float, phase: str, now: float
) -> tuple[int, float, float]:
    """Check a full original phase window plus current valid telemetry."""

    rows = db.execute(
        "SELECT ts,voltage,frequency FROM measurements "
        "WHERE phase=? AND ts>=? AND ts<=? ORDER BY ts",
        (phase, ended, ended + WINDOW_SECONDS),
    ).fetchall()
    count, gap = sample_quality(rows, ended)
    latest = db.execute(
        "SELECT ts,voltage,frequency FROM measurements "
        "WHERE phase=? ORDER BY ts DESC LIMIT 1",
        (phase,),
    ).fetchone()
    if latest is None:
        raise ValueError("current phase has no telemetry")
    if not valid_sample(latest):
        raise ValueError("invalid latest measurement")
    age = now - float(latest[0])
    if not 0 <= age <= MAX_LATEST_AGE_SECONDS:
        raise ValueError("latest telemetry is missing or stale")
    if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        raise ValueError("SQLite integrity failed")
    if db.execute(
        "SELECT COUNT(*) FROM measurements WHERE voltage=0 OR ts>?",
        (now,),
    ).fetchone()[0]:
        raise ValueError("synthetic zero or future sample")
    return count, gap, round(age, 2)


def inspect_database(path: Path, incident_id: int, now: float) -> dict:
    """Inspect one consistent SQLite transaction for physical telemetry."""

    with closing(open_readonly(path)) as db:
        db.execute("BEGIN")
        ended, phase = closed_event(db, incident_id, now)
        count, gap, age = inspect_samples(db, ended, phase, now)
    return {
        "status": "pass",
        "reason": "15min_real_phase_samples_verified",
        "incident_id": incident_id,
        "scope": "telemetry_only",
        "samples_15min": count,
        "max_gap_seconds": gap,
        "latest_sample_age_seconds": age,
        "telegram_delivery_verified": False,
        "release_authorized": False,
    }


def phase_event(
    db: sqlite3.Connection, phase: str, incident_id: int, now: float
) -> tuple[float, float]:
    """Require a closed latest event and no unresolved outage in this phase."""
    row = db.execute(
        "SELECT id,started_at,ended_at FROM outages "
        "WHERE phase=? ORDER BY id DESC LIMIT 1",
        (phase,),
    ).fetchone()
    if row is None or row[0] != incident_id:
        raise ValueError("incident is not latest for the requested phase")
    _, start, end = row
    if (
        end is None
        or db.execute(
            "SELECT 1 FROM outages WHERE phase=? AND ended_at IS NULL LIMIT 1",
            (phase,),
        ).fetchone()
    ):
        raise ValueError("phase has an open outage")
    if not all(math.isfinite(x) for x in (start, end, now)):
        raise ValueError("invalid incident time")
    if not 0 < start < end <= now:
        raise ValueError("invalid incident time")
    if now - end < WINDOW_SECONDS:
        raise ValueError("recovery window is not complete")
    return start, end


def inspect_three_phases(
    path: Path, incident_ids: tuple[int, ...], now: float
) -> dict:
    """Check one simultaneous Pro3EM outage in a single read-only snapshot."""
    if len(incident_ids) != 3 or len(set(incident_ids)) != 3:
        raise ValueError("three distinct incident IDs required")
    details = {}
    starts, ends = [], []
    with closing(open_readonly(path)) as db:
        db.execute("BEGIN")
        for phase, incident_id in zip(PHASES, incident_ids):
            start, end = phase_event(db, phase, incident_id, now)
            count, gap, age = inspect_samples(db, end, phase, now)
            starts.append(start)
            ends.append(end)
            details[phase] = {
                "incident_id": incident_id,
                "samples_15min": count,
                "max_gap_seconds": gap,
                "latest_sample_age_seconds": age,
            }
        if any(
            max(times) - min(times) > MAX_PHASE_EVENT_SKEW_SECONDS
            for times in (starts, ends)
        ):
            raise ValueError("incidents do not form one simultaneous outage")
    return {
        "status": "pass",
        "reason": "15min_all_phase_samples_verified",
        "scope": "telemetry_only",
        "phases": details,
        "telegram_delivery_verified": False,
        "release_authorized": False,
    }


def main() -> int:
    """Return stable PASS/BLOCKED codes without changing production."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--incident-id", type=int)
    mode.add_argument(
        "--incident-ids",
        nargs=3,
        type=int,
        metavar=("L1", "L2", "L3"),
        help="Three IDs for one simultaneous three-phase outage",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        if args.incident_ids is not None:
            ids = tuple(args.incident_ids)
            if any(value < 1 for value in ids):
                raise ValueError("incident IDs must be positive")
            result = inspect_three_phases(args.db, ids, time.time())
        else:
            if args.incident_id < 1:
                raise ValueError("incident ID must be positive")
            result = inspect_database(args.db, args.incident_id, time.time())
    except (ValueError, TypeError, OSError, sqlite3.Error, OverflowError):
        if args.incident_ids is not None:
            result = blocked("all_phase_window_unverified", None)
            del result["incident_id"]
            result["incident_ids"] = args.incident_ids
        else:
            result = blocked("telemetry_window_unverified", args.incident_id)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print(result["status"].upper(), result["reason"])
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
