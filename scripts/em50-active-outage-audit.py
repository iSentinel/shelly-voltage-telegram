#!/usr/bin/env python3
"""Read-only consistency audit for an active Shelly EM-50 outage."""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import closing
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

KYIV = ZoneInfo("Europe/Kyiv")
MAX_RESPONSE = 16384


class Blocked(ValueError):
    """A stable, non-sensitive reason to reject outage evidence."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse a redirect away from the expected loopback endpoint."""

    def redirect_request(self, request, file, code, message, headers, url):
        """Do not follow HTTP redirect responses."""

        return None


def local_json(url: str, route: str) -> tuple[int, dict]:
    """Fetch bounded localhost JSON without proxies or redirects."""

    target = urllib.parse.urlsplit(url)
    if (
        target.scheme != "http"
        or target.hostname not in {"127.0.0.1", "localhost"}
        or target.path != route
        or target.username is not None
        or target.password is not None
        or target.query
        or target.fragment
    ):
        raise Blocked("UNSAFE_ENDPOINT")
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), NoRedirect()
    )
    try:
        response = opener.open(url, timeout=5)
    except urllib.error.HTTPError as exc:
        response = exc
    except (OSError, ValueError):
        raise Blocked("LOCAL_API_UNAVAILABLE") from None
    with response:
        status = response.status
        body = response.read(MAX_RESPONSE + 1)
    if status not in (200, 503) or len(body) > MAX_RESPONSE:
        raise Blocked("LOCAL_API_INVALID")
    try:
        value = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise Blocked("LOCAL_API_INVALID") from None
    if not isinstance(value, dict):
        raise Blocked("LOCAL_API_INVALID")
    return status, value


def database(path: Path) -> sqlite3.Connection:
    """Open one existing regular database, explicitly read-only."""

    if path.is_symlink() or not path.is_file():
        raise Blocked("DATABASE_UNAVAILABLE")
    uri = "file:" + urllib.parse.quote(str(path.resolve())) + "?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=5)


def event_and_sample(db: sqlite3.Connection) -> tuple:
    """Select the latest outage and latest real measurement."""

    event = db.execute(
        "SELECT id,started_at,ended_at,phase "
        "FROM outages ORDER BY id DESC LIMIT 1"
    ).fetchone()
    sample = db.execute(
        "SELECT ts,voltage,frequency,phase "
        "FROM measurements ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not event or not sample:
        raise Blocked("TELEMETRY_MISSING")
    return event, sample


def validate_event(event: tuple, sample: tuple, now: float) -> None:
    """Reject a closed event or inconsistent physical sample ordering."""

    _, started, ended, phase = event
    if ended is not None:
        raise Blocked("INCIDENT_NOT_OPEN")
    if (
        not all(
            isinstance(v, (int, float)) and math.isfinite(v)
            for v in (started, sample[0], sample[1], sample[2])
        )
        or not 0 < sample[0] < started < now + 5
        or sample[3] != phase
        or not 0 < sample[1] < 500
        or not 40 <= sample[2] <= 70
    ):
        raise Blocked("INCIDENT_SEQUENCE")


def inspect_db(path: Path, incident_id: int | None, now: float) -> dict:
    """Verify full-database zero guards and no post-outage samples."""

    with closing(database(path)) as db:
        db.execute("BEGIN")
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise Blocked("DATABASE_INTEGRITY")
        event, sample = event_and_sample(db)
        validate_event(event, sample, now)
        number, started, _, phase = event
        if incident_id is not None and number != incident_id:
            raise Blocked("INCIDENT_ID_MISMATCH")
        post = db.execute(
            "SELECT count(*) FROM measurements WHERE phase=? AND ts>=?",
            (phase, started),
        ).fetchone()[0]
        invalid = db.execute(
            "SELECT count(*) FROM measurements WHERE voltage=0 OR ts>?",
            (now + 5,),
        ).fetchone()[0]
        open_count = db.execute(
            "SELECT count(*) FROM outages WHERE ended_at IS NULL"
        ).fetchone()[0]
    if post or invalid or open_count != 1:
        raise Blocked("POST_OUTAGE_DATA_CONFLICT")
    return {
        "incident_id": number,
        "phase": phase,
        "started_at": float(started),
        "last_sample_at": float(sample[0]),
        "samples_after_start": post,
        "zero_or_future_samples": invalid,
    }


def verify_health(health: dict, ready_code: int, ready: dict,
                  state: dict, now: float) -> None:
    """Require the offline API state to match historical SQLite data."""

    if (
        health.get("connectivity") != "offline"
        or ready_code != 503
        or ready.get("ready") is not False
        or ready.get("reason") != "shelly offline"
    ):
        raise Blocked("METER_NOT_CONFIRMED_OFFLINE")
    try:
        last_success = float(health["last_success"])
    except (KeyError, TypeError, ValueError):
        raise Blocked("HEALTH_TIMESTAMP_INVALID") from None
    if (
        not math.isfinite(last_success)
        or not 30 <= now - last_success <= 14 * 86400
        or abs(last_success - state["last_sample_at"]) > 5
    ):
        raise Blocked("HEALTH_TIMESTAMP_INVALID")
    if health.get("outage_count") != state["incident_id"]:
        raise Blocked("INCIDENT_COUNTER_MISMATCH")


def audit(path: Path, incident_id: int | None, now: float) -> dict:
    """Inspect health before and after an isolated SQLite read."""

    health_code, first = local_json(
        "http://127.0.0.1:8080/health", "/health"
    )
    ready_code, ready = local_json(
        "http://127.0.0.1:8080/ready", "/ready"
    )
    if health_code != 200:
        raise Blocked("HEALTH_API_INVALID")
    state = inspect_db(path, incident_id, now)
    verify_health(first, ready_code, ready, state, now)
    after_code, after = local_json(
        "http://127.0.0.1:8080/health", "/health"
    )
    if (
        after_code != 200
        or after.get("connectivity") != "offline"
        or after.get("last_success") != first.get("last_success")
    ):
        raise Blocked("CONNECTION_CHANGED")
    return {
        "status": "PASS",
        "reason": "ACTIVE_OUTAGE_TELEMETRY_CONSISTENT",
        "incident_id": state["incident_id"],
        "phase": state["phase"],
        "outage_started_kyiv": datetime.fromtimestamp(
            state["started_at"], KYIV
        ).isoformat(timespec="seconds"),
        "last_sample_kyiv": datetime.fromtimestamp(
            state["last_sample_at"], KYIV
        ).isoformat(timespec="seconds"),
        "last_sample_age_s": round(now - state["last_sample_at"], 1),
        "samples_after_start": state["samples_after_start"],
        "zero_or_future_samples": state["zero_or_future_samples"],
        "current_voltage_known": False,
        "telegram_delivery_verified": False,
        "release_authorized": False,
        "production_modified": False,
    }


def main() -> int:
    """Report read-only outage consistency using stable exit codes."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--incident-id", type=int)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        if args.incident_id is not None and args.incident_id < 1:
            raise Blocked("INVALID_INCIDENT_ID")
        result = audit(args.database, args.incident_id, time.time())
    except (Blocked, OSError, OverflowError, sqlite3.Error) as exc:
        reason = str(exc) if isinstance(exc, Blocked) else "AUDIT_ERROR"
        result = {
            "status": "BLOCKED",
            "reason": reason,
            "telegram_delivery_verified": False,
            "release_authorized": False,
            "production_modified": False,
        }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print(result["status"] + ": " + result["reason"])
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
