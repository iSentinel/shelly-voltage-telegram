#!/usr/bin/env python3
"""Check Shelly recovery readiness without network or database writes."""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path
from urllib.parse import quote


def parser() -> argparse.ArgumentParser:
    """Expose safe preflight options to an operator."""

    tool = argparse.ArgumentParser(description=__doc__)
    tool.add_argument("--baseline", type=Path, required=True)
    tool.add_argument("--db", type=Path, required=True)
    tool.add_argument("--at", help="Timezone-aware ISO-8601 observation time")
    tool.add_argument("--max-sample-age-sec", type=int, default=45)
    tool.add_argument("--json", action="store_true")
    return tool


def load_baseline(path: Path) -> dict:
    """Accept only a successful pre-outage recovery baseline."""

    if path.is_symlink() or not path.is_file() or path.stat().st_size > 16384:
        raise ValueError("baseline is missing or oversized")
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError("baseline must be a JSON object")
    if payload.get("kind") != "recovery_baseline":
        raise ValueError("baseline kind is invalid")
    if payload.get("status") != "pass" or payload.get("errors"):
        raise ValueError("baseline is not an accepted PASS")
    state = payload.get("state")
    if not isinstance(state, dict):
        raise ValueError("baseline has no captured state")
    incident = state.get("outage")
    if not isinstance(incident, dict) or incident.get("ended_at") is not None:
        raise ValueError("baseline must capture an open connectivity loss")
    if not state.get("status_message_fingerprint"):
        raise ValueError("Telegram status fingerprint was not captured")
    if state.get("quick_check") != "ok" or state.get("zero_voltage_rows") != 0:
        raise ValueError("baseline database evidence failed integrity")
    if not isinstance(state.get("measurement_count"), int):
        raise ValueError("baseline sample count is missing")
    return state


def read_live(path: Path, phase: str) -> dict:
    """Read event and last valid phase sample from existing SQLite only."""

    if path.is_symlink() or not path.is_file():
        raise ValueError("database is missing or a symlink")
    uri = "file:" + quote(str(path.resolve())) + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=4)) as db:
        event = db.execute(
            "select id,started_at,ended_at,phase from outages "
            "order by id desc limit 1"
        ).fetchone()
        sample = db.execute(
            "select ts,voltage,frequency from measurements "
            "where phase=? and voltage>0 and voltage<500 "
            "and frequency>=40 and frequency<=70 "
            "order by ts desc limit 1",
            (phase,),
        ).fetchone()
        return {
            "event": event,
            "last_valid_sample": sample,
            "quick_check": db.execute("pragma quick_check").fetchone()[0],
            "zero_rows": db.execute(
                "select count(*) from measurements where voltage=0"
            ).fetchone()[0],
            "samples": db.execute(
                "select count(*) from measurements"
            ).fetchone()[0],
        }


def observation_clock(raw: str | None) -> float:
    """Use an explicit timezone when supplied, not the server TZ."""

    if raw is None:
        return time.time()
    observed = datetime.fromisoformat(raw)
    if observed.tzinfo is None:
        raise ValueError("observation must have a timezone offset")
    stamp = observed.timestamp()
    if not math.isfinite(stamp):
        raise ValueError("invalid observation timestamp")
    return stamp


def pending(reason: str, identifier: int) -> dict:
    """Represent an expected offline condition without false alarms."""

    return {
        "status": "pending",
        "reason": reason,
        "incident_id": identifier,
        "current_voltage_v": None,
        "mains_restoration_verified": False,
        "telegram_delivery_verified": False,
    }


def classify(old: dict, live: dict, now: float, max_age: int) -> dict:
    """Accept a recovery candidate only after a fresh valid sample."""

    if not 10 <= max_age <= 300:
        raise ValueError("invalid maximum sample age")
    event = live["event"]
    if live["quick_check"] != "ok" or live["zero_rows"] != 0:
        raise ValueError("database integrity or synthetic zero check failed")
    old_event = old["outage"]
    if event is None or (event[0], event[1], event[3]) != (
        old_event["id"],
        old_event["started_at"],
        old_event["phase"],
    ):
        raise ValueError("latest incident does not match baseline")
    identifier, started, ended, _phase = event
    if ended is None:
        return pending("shelly_connectivity_loss_open", identifier)
    if not math.isfinite(ended) or ended < started or now < ended:
        raise ValueError("invalid event recovery timestamp")
    if live["samples"] <= old["measurement_count"]:
        return pending("no_new_samples_after_baseline", identifier)
    return evaluate_measurement(
        live["last_valid_sample"], ended, now, max_age, identifier
    )


def evaluate_measurement(
    sample: tuple | None,
    ended: float,
    now: float,
    max_age: int,
    identifier: int,
) -> dict:
    """Do not promote a stale or missing sample to recovery proof."""

    if sample is None:
        return pending("no_valid_voltage_sample", identifier)
    ts, voltage, frequency = sample
    age = now - ts
    if not math.isfinite(age) or age < 0 or age > max_age:
        return pending("recent_sample_missing", identifier)
    if ts < ended - 15:
        return pending("sample_not_near_connection_recovery", identifier)
    return {
        "status": "candidate",
        "reason": "fresh_measured_voltage_and_gap_closed",
        "incident_id": identifier,
        "current_voltage_v": round(voltage, 1),
        "frequency_hz": round(frequency, 2),
        "sample_age_sec": round(age, 1),
        "mains_restoration_verified": False,
        "telegram_delivery_verified": False,
        "next_action": "run existing full recovery-acceptance verification",
    }


def main() -> int:
    """Print one bounded, read-only PASS/PENDING/BLOCKED preflight."""

    args = parser().parse_args()
    try:
        clock = observation_clock(args.at)
        baseline = load_baseline(args.baseline)
        state = read_live(args.db, str(baseline["outage"]["phase"]))
        result = classify(baseline, state, clock, args.max_sample_age_sec)
    except (
        ValueError,
        TypeError,
        KeyError,
        OSError,
        OverflowError,
        sqlite3.Error,
        json.JSONDecodeError,
    ) as error:
        result = {"status": "blocked", "reason": type(error).__name__}
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"{result['status'].upper()}: {result['reason']}")
    return 1 if result["status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
