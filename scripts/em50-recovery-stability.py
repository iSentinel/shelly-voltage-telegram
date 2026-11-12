"""Audit fifteen minutes of stable physical Shelly recovery."""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

SOURCE = Path(__file__).with_name("em50-natural-recovery-audit.py")
SPEC = importlib.util.spec_from_file_location("em50_natural", SOURCE)
assert SPEC is not None and SPEC.loader is not None
NATURAL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(NATURAL)
WINDOW_SECONDS = 15 * 60


def read_window(db_path: Path, phase: str, started_at: float, now: float):
    """Read a bounded chronological sample window from SQLite in mode=ro."""

    if not db_path.is_file():
        raise ValueError("SQLite database missing")
    with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as db:
        return db.execute(
            "SELECT ts,voltage,frequency FROM measurements WHERE phase=? "
            "AND ts>=? AND ts<=? ORDER BY ts LIMIT 5001",
            (phase, started_at, now),
        ).fetchall()


def verified_times(rows: list) -> list[float]:
    """Require real, finite and strictly increasing measurement timestamps."""

    values = []
    for timestamp, _, _ in rows:
        if type(timestamp) not in {int, float} or not math.isfinite(timestamp):
            raise ValueError("invalid physical sample timestamp")
        if values and timestamp <= values[-1]:
            raise ValueError("non-increasing physical sample timestamp")
        values.append(float(timestamp))
    return values


def verify_stability(
    rows: list,
    window_start: float,
    now: float,
    max_gap: float,
    min_samples: int,
) -> dict:
    """Reject sparse, invalid or discontinuous physical measurements."""

    if not 5 <= max_gap <= 60 or not 50 <= min_samples <= 180:
        raise ValueError("stability thresholds outside allowed bounds")
    if len(rows) < min_samples or len(rows) > 5000:
        raise ValueError("too few or too many samples for stability")
    times = verified_times(rows)
    gaps = [
        times[0] - window_start,
        now - times[-1],
        *(b - a for a, b in zip(times, times[1:])),
    ]
    if min(gaps) < 0 or max(gaps) > max_gap:
        raise ValueError("physical sample gap exceeds allowed maximum")
    for _, voltage, frequency in rows:
        if (
            type(voltage) not in {int, float}
            or type(frequency) not in {int, float}
            or not math.isfinite(voltage)
            or not math.isfinite(frequency)
            or not 0 < voltage < 500
            or not 40 < frequency < 60
        ):
            raise ValueError("sample contains invalid physical metrics")
    return {
        "window_seconds": WINDOW_SECONDS,
        "sample_count": len(rows),
        "largest_gap_seconds": round(max(gaps), 2),
        "voltage_min": round(min(r[1] for r in rows), 1),
        "voltage_max": round(max(r[1] for r in rows), 1),
    }


def inspect(args: argparse.Namespace, now: float) -> dict:
    """Require fresh online health and an entirely post-recovery window."""

    db_path = Path(args.db)
    state = NATURAL.read_state(db_path, 60)
    health = NATURAL.local_json(args.health_url, "/health")
    ready = NATURAL.local_json(args.ready_url, "/ready")
    NATURAL.check_recovery(state, health, ready, now, 60)
    window_start = now - WINDOW_SECONDS
    if state["end"] > window_start:
        raise ValueError("full fifteen-minute recovery window not elapsed")
    rows = read_window(db_path, state["phase"], window_start, now)
    summary = verify_stability(
        rows, window_start, now, args.max_gap_seconds, args.min_samples
    )
    return {
        "status": "PASS",
        "outage_id": state["outage_id"],
        "closed_before_window": True,
        **summary,
        "telegram_channel_edit_visible": "NOT_VERIFIED",
        "release_authorized": False,
    }


def pending_stability(args: argparse.Namespace, now: float) -> dict | None:
    """Describe an open natural outage before probing offline HTTP."""

    state = NATURAL.read_state(Path(args.db), 60)
    return NATURAL.pending_record(state, now)


def validate_thresholds(args: argparse.Namespace) -> None:
    """Refuse unsafe physical acceptance limits during outage too."""

    if not 5 <= args.max_gap_seconds <= 60:
        raise ValueError("invalid maximum physical sample gap")
    if not 50 <= args.min_samples <= 180:
        raise ValueError("invalid minimum physical sample count")


def emit_pending(pending: dict, json_mode: bool) -> int:
    """Report unavailable recovery while keeping the release blocked."""

    if json_mode:
        print(json.dumps(pending, ensure_ascii=False, sort_keys=True))
    else:
        incident = pending["outage_id"]
        print(f"PENDING recovery_stability incident={incident}")
    return 2


def main() -> int:
    """Print a non-secret stable/unstable decision without side effects."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db", default="/var/lib/shelly-voltage/voltage.sqlite3"
    )
    parser.add_argument("--health-url", default="http://127.0.0.1:8080/health")
    parser.add_argument("--ready-url", default="http://127.0.0.1:8080/ready")
    parser.add_argument("--max-gap-seconds", type=float, default=30)
    parser.add_argument("--min-samples", type=int, default=60)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        now = time.time()
        validate_thresholds(args)
        pending = pending_stability(args, now)
        if pending is not None:
            return emit_pending(pending, args.json)
        result = inspect(args, now)
    except (
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        OSError,
        sqlite3.DatabaseError,
    ) as exc:
        print(f"FAIL recovery_stability {type(exc).__name__}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print(
            f"PASS recovery_stability incident={result['outage_id']} "
            f"samples={result['sample_count']} release_authorized=false"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
