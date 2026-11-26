#!/usr/bin/env python3
"""Audit a continuous safe three-phase window without authorizing cutover."""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import time
import urllib.request
from contextlib import closing
from pathlib import Path

import github_update

PHASES = ("L1", "L2", "L3")
WINDOW = 900
MAX_GAP = 30
MIN_SAMPLES = 60
READY = "http://127.0.0.1:8080/ready"


def finite(value: object) -> bool:
    """Refuse nonnumeric and nonfinite SQLite values."""
    return type(value) in (int, float) and math.isfinite(value)


def phase_report(rows: list, now: float) -> dict:
    """Evaluate coverage, strict chronological order and safe voltage."""
    reasons = []
    if not MIN_SAMPLES <= len(rows) <= 5000:
        reasons.append("SAMPLE_COUNT")
    times = [row[0] for row in rows]
    gap = None
    if not times or not all(finite(t) for t in times):
        reasons.append("INVALID_TIMESTAMPS")
    else:
        gaps = [times[0] - (now - WINDOW), now - times[-1]]
        gaps.extend(b - a for a, b in zip(times, times[1:]))
        gap = round(max(gaps), 3)
        if (
            min(gaps) < 0
            or max(gaps) > MAX_GAP
            or any(b <= a for a, b in zip(times, times[1:]))
        ):
            reasons.append("SAMPLE_GAP_OR_ORDER")
    volts = [row[1] for row in rows]
    if not volts or not all(
        finite(v)
        and github_update.SAFE_VOLTAGE_MIN
        <= v
        <= github_update.SAFE_VOLTAGE_MAX
        for v in volts
    ):
        reasons.append("VOLTAGE_OUTSIDE_BAND")
    if not rows or not all(
        finite(row[2]) and 40 < row[2] < 60 for row in rows
    ):
        reasons.append("INVALID_FREQUENCY")
    valid = [v for v in volts if finite(v)]
    return {
        "samples": len(rows),
        "max_gap_seconds": gap,
        "voltage_min": min(valid, default=None),
        "voltage_max": max(valid, default=None),
        "reason_codes": reasons,
    }


def window_report(database: Path, now: float) -> dict:
    """Read all three phases and outage state in one SQLite snapshot."""
    if not database.is_file() or database.is_symlink():
        raise ValueError("invalid database path")
    with closing(
        sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    ) as db:
        db.execute("BEGIN")
        integrity = db.execute("PRAGMA quick_check").fetchone() == ("ok",)
        outages = db.execute(
            "SELECT COUNT(*) FROM outages WHERE ended_at IS NULL "
            "OR ended_at > ?",
            (now - WINDOW,),
        ).fetchone()[0]
        phases = {}
        for phase in PHASES:
            rows = db.execute(
                "SELECT ts,voltage,frequency FROM measurements "
                "WHERE phase=? AND ts>=? AND ts<=? ORDER BY ts LIMIT 5001",
                (phase, now - WINDOW, now),
            ).fetchall()
            phases[phase] = phase_report(rows, now)
    reasons = []
    if not integrity:
        reasons.append("DATABASE_INTEGRITY")
    if outages:
        reasons.append("OUTAGE_INTERSECTS_WINDOW")
    if any(p["reason_codes"] for p in phases.values()):
        reasons.append("PHASE_WINDOW_UNSAFE")
    return {
        "window_seconds": WINDOW,
        "phases": phases,
        "reason_codes": reasons,
    }


def readiness() -> None:
    """Require exactly three online phases at the fixed local endpoint."""
    with urllib.request.urlopen(READY, timeout=5) as response:
        status = github_update.decode_json_response(response)
    if (
        status.get("ready") is not True
        or status.get("connectivity") != "online"
        or github_update.ready_phases(status) != PHASES
    ):
        raise ValueError("three-phase readiness not confirmed")


def audit(database: Path) -> dict:
    """Combine the stricter window with all stock updater protections."""
    report = window_report(database, time.time())
    try:
        readiness()
        github_update.preflight(database, READY)
    except (OSError, ValueError, sqlite3.Error):
        report["reason_codes"].append("STOCK_PREFLIGHT_OR_READINESS")
    report["status"] = (
        "BLOCKED" if report["reason_codes"] else "PASS_WINDOW_ONLY"
    )
    report["deployment_authorized"] = False
    report["sqlite_modified"] = False
    return report


def main() -> int:
    """Report a stable read-only decision with explicit exit status."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--jsonl", action="store_true")
    args = parser.parse_args()
    try:
        report = audit(args.database)
    except (OSError, ValueError, sqlite3.Error):
        report = {
            "status": "BLOCKED",
            "reason_codes": ["WINDOW_UNAVAILABLE"],
            "deployment_authorized": False,
            "sqlite_modified": False,
        }
    print(json.dumps(report, separators=(",", ":") if args.jsonl else None))
    return 0 if report["status"] == "PASS_WINDOW_ONLY" else 3


if __name__ == "__main__":
    raise SystemExit(main())
