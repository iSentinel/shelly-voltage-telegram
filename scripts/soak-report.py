#!/usr/bin/env python3
"""Summarize samples and confirmed outages over a selected time window."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def parser() -> argparse.ArgumentParser:
    """Build CLI arguments."""

    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--db", default="/var/lib/shelly-voltage/voltage.sqlite3")
    cli.add_argument("--hours", type=float, default=24.0)
    cli.add_argument("--expected-interval", type=float, default=10.0)
    cli.add_argument("--timezone", default="Europe/Kyiv")
    cli.add_argument("--json", action="store_true")
    return cli


def table_exists(db: sqlite3.Connection, name: str) -> bool:
    """Return whether a SQLite table exists."""

    row = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return bool(row)


def schema_version(db: sqlite3.Connection) -> int:
    """Read schema version while remaining compatible with legacy databases."""

    if not table_exists(db, "meta"):
        return 0
    row = db.execute(
        "SELECT value FROM meta WHERE key='schema_version'"
    ).fetchone()
    return int(row[0]) if row else 0


def read_data(
    path: Path,
    cutoff: float,
    now: float,
) -> tuple[str, int, list[tuple], list[tuple]]:
    """Read samples, outage rows, schema version and integrity status."""

    uri = path.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as db:
        integrity = str(db.execute("PRAGMA quick_check").fetchone()[0])
        version = schema_version(db)
        rows = db.execute(
            """
            SELECT ts, voltage, frequency FROM measurements
            WHERE ts >= ? AND ts <= ? ORDER BY ts ASC
            """,
            (cutoff, now),
        ).fetchall()
        outages: list[tuple] = []
        if table_exists(db, "outages"):
            outages = db.execute(
                """
                SELECT started_at, ended_at FROM outages
                WHERE started_at <= ? AND COALESCE(ended_at, ?) >= ?
                ORDER BY started_at ASC
                """,
                (now, now, cutoff),
            ).fetchall()
    return integrity, version, rows, outages


def gaps(rows: list[tuple], expected: float) -> dict[str, float | int]:
    """Calculate sample-gap statistics without classifying outages."""

    values = [float(b[0]) - float(a[0]) for a, b in zip(rows, rows[1:])]
    threshold = max(30.0, expected * 3)
    return {
        "max_sample_gap_seconds": round(max(values), 3) if values else 0.0,
        "sample_gaps_over_threshold": sum(item > threshold for item in values),
        "sample_gap_threshold_seconds": threshold,
    }


def sample_timing(rows: list[tuple], now: float) -> dict[str, float | None]:
    """Return observed sample span and age of the latest sample."""

    if not rows:
        return {"sample_span_hours": 0.0, "last_sample_age_seconds": None}
    first = float(rows[0][0])
    last = float(rows[-1][0])
    return {
        "sample_span_hours": round(max(0.0, last - first) / 3600, 3),
        "last_sample_age_seconds": round(max(0.0, now - last), 3),
    }


def aggregate(rows: list[tuple]) -> dict[str, float | int | None]:
    """Calculate voltage/frequency aggregate values."""

    if not rows:
        return {
            "samples": 0,
            "voltage_min": None,
            "voltage_max": None,
            "voltage_avg": None,
            "frequency_min": None,
            "frequency_max": None,
            "frequency_avg": None,
        }
    voltage = [float(row[1]) for row in rows]
    frequency = [float(row[2]) for row in rows]
    return {
        "samples": len(rows),
        "voltage_min": round(min(voltage), 3),
        "voltage_max": round(max(voltage), 3),
        "voltage_avg": round(sum(voltage) / len(voltage), 3),
        "frequency_min": round(min(frequency), 3),
        "frequency_max": round(max(frequency), 3),
        "frequency_avg": round(sum(frequency) / len(frequency), 3),
    }


def local_time(timestamp: float | None, zone: ZoneInfo) -> str | None:
    """Format one timestamp in the requested timezone."""

    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, zone).isoformat(
        timespec="seconds"
    )


def outage_report(
    rows: list[tuple],
    cutoff: float,
    now: float,
    zone: ZoneInfo,
) -> dict[str, object]:
    """Summarize confirmed outage rows overlapping the report window."""

    duration = 0.0
    intervals: list[dict[str, object]] = []
    for started_at, ended_at in rows:
        started = float(started_at)
        ended = None if ended_at is None else float(ended_at)
        overlap_start = max(started, cutoff)
        overlap_end = min(ended if ended is not None else now, now)
        overlap = max(0.0, overlap_end - overlap_start)
        duration += overlap
        intervals.append(
            {
                "started_at": local_time(started, zone),
                "ended_at": local_time(ended, zone),
                "open": ended is None,
                "window_overlap_seconds": round(overlap, 3),
            }
        )
    return {
        "confirmed_outages": len(rows),
        "confirmed_open_outages": sum(row[1] is None for row in rows),
        "confirmed_outage_seconds": round(duration, 3),
        "confirmed_outage_intervals": intervals,
    }


def build_report(
    args: argparse.Namespace,
    now: float | None = None,
) -> dict[str, object]:
    """Build a complete soak report."""

    current = time.time() if now is None else now
    cutoff = current - args.hours * 3600
    integrity, version, rows, outages = read_data(
        Path(args.db), cutoff, current
    )
    zone = ZoneInfo(args.timezone)
    report: dict[str, object] = {
        "window_hours": args.hours,
        "window_start": local_time(cutoff, zone),
        "window_end": local_time(current, zone),
        "sqlite_quick_check": integrity,
        "schema_version": version,
        "first_sample": local_time(rows[0][0], zone) if rows else None,
        "last_sample": local_time(rows[-1][0], zone) if rows else None,
        "sample_gap_interpretation": (
            "sample gaps are diagnostic only; confirmed outages come from "
            "the outages table"
        ),
        "confirmed_outage_semantics": (
            "starts when offline hysteresis is satisfied "
            "and ends when recovery "
            "hysteresis is satisfied; not exact mains-loss boundaries"
        ),
    }
    report.update(aggregate(rows))
    report.update(sample_timing(rows, current))
    report.update(gaps(rows, args.expected_interval))
    report.update(outage_report(outages, cutoff, current, zone))
    return report


def print_text(report: dict[str, object]) -> None:
    """Print a stable human-readable summary."""

    for key, value in report.items():
        print(f"{key}={value}")


def main() -> int:
    """CLI entrypoint."""

    args = parser().parse_args()
    if args.hours <= 0 or args.expected_interval <= 0:
        raise SystemExit("--hours and --expected-interval must be > 0")
    try:
        report = build_report(args)
    except (OSError, sqlite3.Error) as exc:
        print(f"ERROR: cannot read database {args.db}: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print_text(report)
    return 0 if report["sqlite_quick_check"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
