#!/usr/bin/env python3
"""Replay optional low-voltage advisory rules against read-only SQLite."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from voltage_warning import (  # noqa: E402
    WarningPolicy,
    WarningState,
    observe_warning,
    validate_policy,
)

MAX_SAMPLES = 8640


def parse_args() -> argparse.Namespace:
    """Parse bounded simulation settings without side effects."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--hours", type=int, default=12)
    parser.add_argument("--below-v", type=float, default=207.0)
    parser.add_argument("--clear-v", type=float, default=210.0)
    parser.add_argument("--dwell-sec", type=int, default=180)
    parser.add_argument("--clear-sec", type=int, default=120)
    parser.add_argument("--gap-sec", type=int, default=45)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def read_samples(db_path: Path, hours: int) -> list[tuple[float, float]]:
    """Read a bounded historical window using SQLite mode=ro."""

    if not db_path.is_file() or db_path.is_symlink():
        raise ValueError("database must be a regular, non-symlink file")
    uri = "file:" + quote(str(db_path.resolve())) + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=4)) as db:
        latest = db.execute("select max(ts) from measurements").fetchone()[0]
        if latest is None:
            raise ValueError("no recorded voltage measurements")
        rows = db.execute(
            "select ts, voltage from measurements "
            "where ts >= ? and ts <= ? order by ts limit ?",
            (latest - hours * 3600, latest, MAX_SAMPLES + 1),
        ).fetchall()
    if len(rows) > MAX_SAMPLES:
        raise ValueError("historical sample count exceeds replay limit")
    return rows


def replay(
    rows: list[tuple[float, float]],
    policy: WarningPolicy,
) -> dict:
    """Simulate advisory events without touching production monitoring."""

    state = WarningState()
    events = []
    for stamp, voltage in rows:
        state, event = observe_warning(state, voltage, stamp, policy)
        if event is not None:
            timestamp = datetime.fromtimestamp(
                stamp, ZoneInfo("Europe/Kyiv")
            ).isoformat()
            events.append({"event": event, "at_kyiv": timestamp})
    return {
        "status": "pass",
        "read_only": True,
        "samples": len(rows),
        "enabled_for_simulation_only": policy.enabled,
        "warning_started": sum(
            entry["event"] == "warning_started" for entry in events
        ),
        "warning_cleared": sum(
            entry["event"] == "warning_cleared" for entry in events
        ),
        "warning_still_active": state.active,
        "events": events,
    }


def main() -> int:
    """Validate policy, replay once and emit reproducible PASS/FAIL."""

    args = parse_args()
    try:
        if args.hours < 1 or args.hours > 24:
            raise ValueError("--hours must be between 1 and 24")
        policy = WarningPolicy(
            enabled=True,
            below_v=args.below_v,
            clear_v=args.clear_v,
            dwell_sec=args.dwell_sec,
            clear_sec=args.clear_sec,
            max_sample_gap_sec=args.gap_sec,
        )
        validate_policy(policy)
        result = replay(read_samples(args.db, args.hours), policy)
    except (ValueError, OSError, sqlite3.Error, OverflowError) as error:
        result = {"status": "fail", "reason": type(error).__name__}
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"{result['status'].upper()}: low-voltage replay")
        if result["status"] == "pass":
            print("Samples:", result["samples"])
            print("Started:", result["warning_started"])
            print("Cleared:", result["warning_cleared"])
        else:
            print("Reason:", result["reason"])
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
