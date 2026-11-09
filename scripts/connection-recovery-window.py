#!/usr/bin/env python3
"""Verify a natural 15-minute Shelly connection recovery read-only."""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import time
import urllib.error
import urllib.request
from contextlib import closing
from pathlib import Path
from urllib.parse import quote

WINDOW_SECONDS = 15 * 60
MAX_SAMPLE_GAP_SECONDS = 90
MAX_LATEST_SAMPLE_AGE_SECONDS = 45


def parser() -> argparse.ArgumentParser:
    """Accept explicit evidence files and read-only local endpoints."""

    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--db", required=True, type=Path)
    cli.add_argument("--baseline", required=True, type=Path)
    cli.add_argument("--verification", required=True, type=Path)
    cli.add_argument("--health-url", default="http://127.0.0.1:8080/health")
    cli.add_argument("--ready-url", default="http://127.0.0.1:8080/ready")
    cli.add_argument("--json", action="store_true")
    return cli


def read_evidence(path: Path) -> dict:
    """Read a bounded existing evidence file without following symlinks."""

    if path.is_symlink() or not path.is_file():
        raise ValueError("evidence must be an existing regular file")
    if path.stat().st_size > 32768:
        raise ValueError("evidence file exceeds size limit")
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError("evidence must be an object")
    return data


def accepted_incident(baseline: dict, recovery: dict) -> dict:
    """Require the same exact natural incident and accepted verification."""

    if baseline.get("kind") != "recovery_baseline":
        raise ValueError("incorrect baseline kind")
    if baseline.get("status") != "pass" or baseline.get("errors"):
        raise ValueError("baseline was not accepted")
    if recovery.get("kind") != "recovery_verification":
        raise ValueError("incorrect verification kind")
    if recovery.get("status") != "pass" or recovery.get("errors"):
        raise ValueError("recovery verification has not passed")
    old = baseline["state"]["outage"]
    current = recovery["state"]["outage"]
    identity = ("id", "started_at", "phase")
    if any(old.get(k) != current.get(k) for k in identity):
        raise ValueError("recovery verification incident mismatch")
    if old.get("ended_at") is not None or current.get("ended_at") is None:
        raise ValueError("baseline must be open and recovery closed")
    if recovery["state"].get("status_message_rotated") is not True:
        raise ValueError("Telegram status rotation not verified")
    if recovery.get("health", {}).get("connectivity") != "online":
        raise ValueError("recovery was not online")
    if recovery.get("ready", {}).get("ready") is not True:
        raise ValueError("recovery was not ready")
    return current


def open_readonly(path: Path) -> sqlite3.Connection:
    """Open only existing SQLite databases without following symlinks."""

    if path.is_symlink() or not path.is_file():
        raise ValueError("database missing or symbolic link")
    uri = "file:" + quote(str(path.resolve())) + "?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=5)


def valid_phase_sample(row: tuple) -> bool:
    """Accept only plausible finite readings of the monitored phase."""

    try:
        voltage, frequency = float(row[1]), float(row[2])
    except (TypeError, ValueError, OverflowError):
        return False
    return (
        math.isfinite(voltage)
        and math.isfinite(frequency)
        and 0 < voltage < 500
        and 40 <= frequency <= 70
    )


def phase_window_samples(
    db: sqlite3.Connection, phase: str, started: float, ended: float
) -> tuple[list[float], int]:
    """Keep every raw sample, including the invalid ones SQL once hid."""

    rows = db.execute(
        "SELECT ts,voltage,frequency FROM measurements WHERE phase=? "
        "AND ts>=? AND ts<=? ORDER BY ts",
        (phase, started, ended),
    ).fetchall()
    stamps = [float(row[0]) for row in rows]
    invalid = sum(not valid_phase_sample(row) for row in rows)
    return stamps, invalid


def last_phase_sample(
    db: sqlite3.Connection, phase: str, now: float
) -> tuple[float | None, bool]:
    """Do not replace an invalid newest reading with an older valid one."""

    row = db.execute(
        "SELECT ts,voltage,frequency FROM measurements WHERE phase=? "
        "AND ts<=? ORDER BY ts DESC LIMIT 1",
        (phase, now),
    ).fetchone()
    if row is None:
        return None, False
    stamp = float(row[0])
    return stamp, math.isfinite(stamp) and valid_phase_sample(row)


def read_state(path: Path, incident: dict, now: float) -> dict:
    """Capture one consistent read transaction of connectivity data."""

    end = float(incident["ended_at"])
    phase = incident["phase"]
    with closing(open_readonly(path)) as db:
        db.execute("BEGIN")
        event = db.execute(
            "SELECT id,started_at,ended_at,phase "
            "FROM outages ORDER BY id DESC LIMIT 1"
        ).fetchone()
        samples, invalid = phase_window_samples(
            db, phase, end, min(now, end + WINDOW_SECONDS)
        )
        latest, latest_valid = last_phase_sample(db, phase, now)
        return {
            "event": event,
            "samples": samples,
            "invalid_window_samples": invalid,
            "latest": latest,
            "latest_sample_valid": latest_valid,
            "future_samples": db.execute(
                "SELECT COUNT(*) FROM measurements WHERE phase=? AND ts>?",
                (phase, now),
            ).fetchone()[0],
            "integrity": db.execute("PRAGMA quick_check").fetchone()[0],
            "zero_count": db.execute(
                "SELECT COUNT(*) FROM measurements WHERE voltage=0"
            ).fetchone()[0],
        }


def fetch_endpoint(url: str) -> tuple[int, dict]:
    """Read one local JSON endpoint without performing an HTTP write."""

    try:
        with urllib.request.urlopen(url, timeout=4) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc)


def sample_window_reason(
    stamps: list[float], ended: float, window_end: float
) -> tuple[str | None, float | None]:
    """Reject missing or large telemetry gaps during the 15 minutes."""

    if not stamps:
        return "no valid same-phase voltage samples", None
    prior = ended
    maximum = 0.0
    for stamp in stamps:
        if not math.isfinite(stamp) or stamp < prior:
            return "invalid same-phase sample timestamp", None
        maximum = max(maximum, stamp - prior)
        prior = stamp
    maximum = max(maximum, window_end - prior)
    if maximum > MAX_SAMPLE_GAP_SECONDS:
        return "same-phase sample gap exceeds 90 seconds", maximum
    return None, maximum


def sample_quality_failures(live: dict) -> list[str]:
    """Reject anomalous raw samples even when other samples are valid."""

    errors = []
    if live["invalid_window_samples"]:
        errors.append("invalid same-phase measurement during recovery window")
    if not live["latest_sample_valid"]:
        errors.append("latest same-phase measurement is invalid")
    return errors


def evaluate_failures(
    incident: dict,
    live: dict,
    health: tuple,
    ready: tuple,
    now: float,
    age: float | None,
    sample_reason: str | None,
) -> list[str]:
    """Return safe explanations for each failed recovery prerequisite."""

    identity = (
        incident["id"],
        incident["started_at"],
        float(incident["ended_at"]),
        incident["phase"],
    )
    checks = (
        (now < float(incident["ended_at"]), "recovery time is in future"),
        (live["event"] != identity, "new or modified connectivity loss"),
        (live["integrity"] != "ok", "SQLite integrity failed"),
        (live["zero_count"] != 0, "synthetic zero-voltage samples"),
        (live["future_samples"] != 0, "future-dated phase samples"),
        (
            health[0] != 200 or health[1].get("connectivity") != "online",
            "Shelly not online",
        ),
        (
            ready[0] != 200 or ready[1].get("ready") is not True,
            "monitor not ready",
        ),
        (
            age is None or age < 0 or age > MAX_LATEST_SAMPLE_AGE_SECONDS,
            "current same-phase measurement is stale",
        ),
        (sample_reason is not None, sample_reason),
    )
    return [
        message for condition, message in checks if condition
    ] + sample_quality_failures(live)


def verdict_reason(status: str, failures: list[str]) -> str:
    """Give one concise non-sensitive reason for the current gate."""

    if failures:
        return failures[0]
    return (
        "15-minute sample window not elapsed"
        if status == "pending"
        else "15-minute sample window verified"
    )


def evaluate(
    incident: dict, live: dict, health: tuple, ready: tuple, now: float
) -> dict:
    """Distinguish pending time from real 15-minute observation PASS."""

    ended = float(incident["ended_at"])
    result = {
        "incident_id": incident["id"],
        "phase": incident["phase"],
        "read_only": True,
        "subscriber_visible_telegram_delivery": False,
        "minutes_since_recovery": round((now - ended) / 60, 2),
        "same_phase_samples_in_window": len(live["samples"]),
    }
    reason, gap = sample_window_reason(
        live["samples"], ended, min(now, ended + WINDOW_SECONDS)
    )
    age = now - live["latest"] if live["latest"] is not None else None
    result["max_sample_gap_seconds"] = (
        round(gap, 2) if gap is not None else None
    )
    result["last_sample_age_seconds"] = (
        round(age, 2) if age is not None else None
    )
    failures = evaluate_failures(
        incident, live, health, ready, now, age, reason
    )
    result["status"] = (
        "blocked"
        if failures
        else ("pending" if now - ended < WINDOW_SECONDS else "pass")
    )
    result["reason"] = verdict_reason(result["status"], failures)
    return result


def main() -> int:
    """Report reproducible PASS/PENDING/BLOCKED with no stored-data changes."""

    args = parser().parse_args()
    try:
        now = time.time()
        evidence = accepted_incident(
            read_evidence(args.baseline), read_evidence(args.verification)
        )
        live = read_state(args.db, evidence, now)
        report = evaluate(
            evidence,
            live,
            fetch_endpoint(args.health_url),
            fetch_endpoint(args.ready_url),
            now,
        )
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error) as exc:
        report = {"status": "blocked", "reason": type(exc).__name__}
    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        print(f"{report['status'].upper()}: {report['reason']}")
    return 1 if report["status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
