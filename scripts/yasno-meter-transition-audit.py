#!/usr/bin/env python3
"""Audit physical Shelly continuity across a verified YASNO forecast edge."""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import stat
import sys
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from yasno_cache import read_verified_cache  # noqa: E402
from yasno_schedule import parse_schedule  # noqa: E402

KYIV = ZoneInfo("Europe/Kyiv")
MAX_GAP_SECONDS = 30


def arguments() -> argparse.Namespace:
    """Read a target time and trusted read-only local data sources."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--yasno-cache", type=Path, required=True)
    parser.add_argument("--boundary", required=True)
    parser.add_argument("--group", default="10.1")
    parser.add_argument("--phase", choices=("L1", "L2", "L3"), default="L1")
    parser.add_argument("--expect", choices=("X:O", "O:X"), required=True)
    parser.add_argument("--minutes", type=int, default=15)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def boundary_time(value: str, now: datetime) -> datetime:
    """Require a real Kyiv minute boundary on the currently audited day."""

    try:
        when = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError("BOUNDARY_INVALID") from None
    if when.tzinfo is None or when.utcoffset() is None:
        raise ValueError("BOUNDARY_TZ_MISSING")
    if when.astimezone(KYIV).utcoffset() != when.utcoffset():
        raise ValueError("BOUNDARY_KYIV_OFFSET_MISMATCH")
    local = when.astimezone(KYIV)
    if local.date() != now.astimezone(KYIV).date():
        raise ValueError("BOUNDARY_NOT_TODAY")
    if local.minute not in (0, 30) or local.second or local.microsecond:
        raise ValueError("BOUNDARY_NOT_HALF_HOUR")
    if local.hour == 0 and local.minute == 0:
        raise ValueError("BOUNDARY_FIRST_SLOT_UNSUPPORTED")
    return local


def expected_forecast(day, boundary: datetime, expected: str) -> dict:
    """Verify a genuine scheduled change, independent of Shelly readings."""

    slot = boundary.hour * 2 + boundary.minute // 30
    observed = day.bars[slot - 1] + ":" + day.bars[slot]
    if observed != expected:
        raise ValueError("FORECAST_TRANSITION_MISMATCH")
    return {
        "day": day.day,
        "group": day.group,
        "forecast_before": day.bars[slot - 1],
        "forecast_after": day.bars[slot],
        "schedule_source": day.source,
    }


def read_samples(
    database: Path, start: float, end: float, phase: str = "L1"
) -> tuple:
    """Read unchanged SQLite history without creating or writing files."""

    info = database.lstat()
    if not stat.S_ISREG(info.st_mode) or database.is_symlink():
        raise ValueError("DATABASE_UNSAFE")
    uri = "file:" + quote(str(database.resolve())) + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as db:
        # One read transaction keeps both SELECTs on the same WAL snapshot.
        db.execute("BEGIN")
        rows = db.execute(
            "select ts,voltage,frequency from measurements "
            "where phase = ? and ts >= ? and ts < ? order by ts",
            (phase, start, end),
        ).fetchall()
        outages = db.execute(
            "select count(*) from outages where phase = ? "
            "and started_at < ? and (ended_at is null or ended_at > ?)",
            (phase, end, start),
        ).fetchone()[0]
        db.rollback()
    return rows, outages


def numeric_sample(row: tuple) -> bool:
    """Allow only finite numeric telemetry values from SQLite."""

    return all(
        type(value) in (int, float) and math.isfinite(value) for value in row
    )


def physical_result(
    rows: list,
    outages: int,
    start: float,
    boundary: float,
    end: float,
    minutes: int,
) -> dict:
    """Require sufficient healthy samples on both sides of the edge."""

    if any(not numeric_sample(row) for row in rows):
        raise ValueError("INVALID_PHYSICAL_SAMPLE")
    before = [row for row in rows if row[0] < boundary]
    after = [row for row in rows if row[0] >= boundary]
    gaps = [b[0] - a[0] for a, b in zip(rows, rows[1:])]
    boundary_gaps = [rows[0][0] - start, end - rows[-1][0]] if rows else []
    maximum = max(gaps + boundary_gaps, default=float("inf"))
    required = minutes * 4
    reasons = []
    if min(len(before), len(after)) < required:
        reasons.append("INSUFFICIENT_SAMPLES")
    if maximum > MAX_GAP_SECONDS:
        reasons.append("MEASUREMENT_GAP")
    if any(gap <= 0 for gap in gaps):
        reasons.append("REPEATED_TIMESTAMP")
    if outages:
        reasons.append("REAL_OUTAGE_RECORDED")
    if any(v <= 0 or f <= 0 for _, v, f in rows):
        reasons.append("INVALID_PHYSICAL_SAMPLE")
    return {
        "status": "FAIL" if reasons else "PASS",
        "reasons": reasons,
        "before_samples": len(before),
        "after_samples": len(after),
        "max_gap_seconds": round(maximum, 3) if rows else None,
        "min_voltage_v": min((x[1] for x in rows), default=None),
        "max_voltage_v": max((x[1] for x in rows), default=None),
        "outages_overlap_window": outages,
        "physical_outage_inferred_from_forecast": False,
    }


def classify_edge(physical: dict, expected: str) -> dict:
    """Separate measured continuity from the forecast's planned switch."""

    if physical["status"] != "PASS":
        return {
            "meter_voltage_continuity": "UNVERIFIED",
            "forecast_vs_meter": "NOT_DETERMINED",
        }
    return {
        "meter_voltage_continuity": "CONFIRMED",
        "forecast_vs_meter": (
            "PLANNED_CUT_NOT_OBSERVED"
            if expected == "O:X"
            else "PLANNED_RESTORATION_NOT_OBSERVED"
        ),
    }


def completed_window(
    args: argparse.Namespace,
    summary: dict,
    start: datetime,
    boundary: datetime,
    end: datetime,
) -> dict:
    """Summarize measured voltage independently from the outage plan."""

    rows, outages = read_samples(
        args.database, start.timestamp(), end.timestamp(), args.phase
    )
    physical = physical_result(
        rows,
        outages,
        start.timestamp(),
        boundary.timestamp(),
        end.timestamp(),
        args.minutes,
    )
    return {**summary, **physical, **classify_edge(physical, args.expect)}


def inspect(args: argparse.Namespace, now: datetime) -> dict:
    """Return PENDING until a complete real post-boundary window exists."""

    if not 1 <= args.minutes <= 60:
        raise ValueError("WINDOW_INVALID")
    boundary = boundary_time(args.boundary, now)
    raw, captured = read_verified_cache(str(args.yasno_cache))
    day = parse_schedule(raw, args.group, now, captured)
    details = expected_forecast(day, boundary, args.expect)
    end = boundary + timedelta(minutes=args.minutes)
    summary = {
        **details,
        "boundary_kyiv": boundary.isoformat(),
        "phase": args.phase,
        "window_minutes_each_side": args.minutes,
        "observation_complete_kyiv": end.isoformat(),
        "writes_performed": False,
        "telegram_mutated": False,
    }
    if now < end:
        return {
            **summary,
            "status": "PENDING",
            "reason": "WINDOW_NOT_COMPLETE",
            "meter_voltage_continuity": "UNVERIFIED",
            "forecast_vs_meter": "WINDOW_NOT_COMPLETE",
        }
    start = boundary - timedelta(minutes=args.minutes)
    return completed_window(args, summary, start, boundary, end)


def main() -> int:
    """Exit 0 for PASS, 2 for PENDING/FAIL, without modifying anything."""

    args = arguments()
    try:
        result = inspect(args, datetime.now(timezone.utc))
    except (OSError, ValueError, sqlite3.Error) as exc:
        detail = str(exc)
        safe = {
            "WINDOW_INVALID",
            "BOUNDARY_INVALID",
            "BOUNDARY_TZ_MISSING",
            "BOUNDARY_KYIV_OFFSET_MISMATCH",
            "BOUNDARY_NOT_TODAY",
            "BOUNDARY_NOT_HALF_HOUR",
            "BOUNDARY_FIRST_SLOT_UNSUPPORTED",
            "FORECAST_TRANSITION_MISMATCH",
            "DATABASE_UNSAFE",
            "INVALID_PHYSICAL_SAMPLE",
        }
        result = {
            "status": "FAIL",
            "reason": detail if detail in safe else "AUDIT_UNAVAILABLE",
            "writes_performed": False,
            "telegram_mutated": False,
        }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print("YASNO/METER BOUNDARY: " + result["status"])
        print("See --json for sanitized details")
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
