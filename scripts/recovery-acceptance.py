#!/usr/bin/env python3
"""Capture and verify a read-only mains-recovery acceptance checkpoint."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from contextlib import closing
from pathlib import Path

DEFAULT_DB = Path("/data/voltage.sqlite3")
DEFAULT_HEALTH = "http://127.0.0.1:8080/health"
DEFAULT_READY = "http://127.0.0.1:8080/ready"


def open_db(path: Path) -> sqlite3.Connection:
    """Open an existing SQLite database in read-only mode."""

    if not path.is_file():
        raise ValueError(f"database does not exist: {path}")
    uri = f"file:{path.resolve()}?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=5)


def scalar(db: sqlite3.Connection, sql: str, params: tuple = ()) -> object:
    """Return the first value from one query."""

    row = db.execute(sql, params).fetchone()
    return row[0] if row else None


def current_db_state(path: Path) -> dict[str, object]:
    """Read the recovery-relevant database state without modifying it."""

    with closing(open_db(path)) as db:
        outage = db.execute(
            "SELECT id,started_at,ended_at,phase FROM outages "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return {
            "quick_check": scalar(db, "PRAGMA quick_check"),
            "zero_voltage_rows": scalar(
                db, "SELECT COUNT(*) FROM measurements WHERE voltage=0"
            ),
            "measurement_count": scalar(
                db, "SELECT COUNT(*) FROM measurements"
            ),
            "status_message_id": scalar(
                db,
                "SELECT value FROM meta WHERE "
                "key='telegram_status_message_id'",
            ),
            "outage": None
            if outage is None
            else {
                "id": outage[0],
                "started_at": outage[1],
                "ended_at": outage[2],
                "phase": outage[3],
            },
        }


def fetch_json(url: str, timeout: float) -> tuple[int, dict[str, object]]:
    """Fetch one local JSON endpoint and return HTTP status plus payload."""

    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc)


def status_message_fingerprint(value: object) -> str:
    """Return a one-way fingerprint for a Telegram status message ID."""

    text = "" if value is None else str(value)
    return hashlib.sha256(text.encode("utf-8")).hexdigest() if text else ""


def baseline_state(state: dict[str, object]) -> dict[str, object]:
    """Return baseline state without storing a raw Telegram message ID."""

    safe = dict(state)
    message_id = safe.pop("status_message_id", None)
    safe["status_message_fingerprint"] = status_message_fingerprint(message_id)
    return safe


def baseline_payload(args: argparse.Namespace) -> dict[str, object]:
    """Create a baseline while the outage is still open."""

    state = current_db_state(args.db)
    outage = state["outage"]
    errors = []
    if state["quick_check"] != "ok":
        errors.append("SQLite quick_check is not ok")
    if state["zero_voltage_rows"] != 0:
        errors.append("synthetic zero-voltage rows already exist")
    if not outage or outage["ended_at"] is not None:
        errors.append("there is no open outage to track")
    if not state["status_message_id"]:
        errors.append("stable Telegram status message ID is missing")
    return {
        "kind": "recovery_baseline",
        "created_at": time.time(),
        "database": str(args.db),
        "state": baseline_state(state),
        "status": "pass" if not errors else "fail",
        "errors": errors,
    }


def load_baseline(path: Path) -> dict[str, object]:
    """Load a previously captured recovery baseline."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read baseline: {path}") from exc
    if payload.get("kind") != "recovery_baseline":
        raise ValueError("file is not a recovery baseline")
    state = payload.get("state")
    valid_state = isinstance(state, dict) and isinstance(
        state.get("outage"), dict
    )
    if not valid_state:
        raise ValueError("recovery baseline has no outage state")
    return payload


SAFE_HEALTH_KEYS = (
    "version",
    "connectivity",
    "consecutive_failures",
    "consecutive_successes",
    "last_success",
    "last_transition",
    "offline_since",
    "outage_count",
    "rpc_ms",
    "started_at",
    "maintenance",
)


def safe_health_payload(health: dict[str, object]) -> dict[str, object]:
    """Return health diagnostics without deployment-specific error text."""

    safe = {key: health[key] for key in SAFE_HEALTH_KEYS if key in health}
    phases = health.get("phases")
    if isinstance(phases, dict):
        safe["phases"] = {
            str(name): safe_health_payload(value)
            for name, value in phases.items()
            if isinstance(value, dict)
        }
    return safe


def outage_matches(
    old_outage: dict[str, object], outage: dict[str, object] | None
) -> bool:
    """Return whether current state is the exact captured outage."""

    if outage is None:
        return False
    keys = ("id", "started_at", "phase")
    return all(outage.get(key) == old_outage.get(key) for key in keys)


def baseline_message_fingerprint(baseline: dict[str, object]) -> str:
    """Read a new fingerprint or derive one from a legacy baseline."""

    state = baseline["state"]
    fingerprint = state.get("status_message_fingerprint")
    if fingerprint:
        return str(fingerprint)
    return status_message_fingerprint(state.get("status_message_id"))


def status_message_rotated(
    baseline: dict[str, object], current_id: object
) -> bool:
    """Compare the current Telegram status ID with the captured baseline."""

    old_fingerprint = baseline_message_fingerprint(baseline)
    current_fingerprint = status_message_fingerprint(current_id)
    return bool(
        old_fingerprint
        and current_fingerprint
        and current_fingerprint != old_fingerprint
    )


def safe_verification_state(
    state: dict[str, object], baseline: dict[str, object]
) -> dict[str, object]:
    """Return verification state without the Telegram message identifier."""

    safe = dict(state)
    current_id = safe.pop("status_message_id", None)
    safe["status_message_present"] = bool(current_id)
    safe["status_message_rotated"] = status_message_rotated(
        baseline, current_id
    )
    return safe


def recovery_errors(
    baseline: dict[str, object],
    state: dict[str, object],
    health: dict[str, object],
) -> list[str]:
    """Compare current state with one outage baseline."""

    errors = []
    old_state = baseline["state"]
    old_outage = old_state["outage"]
    outage = state["outage"]
    if state["quick_check"] != "ok" or state["zero_voltage_rows"] != 0:
        errors.append("database integrity or zero-voltage check failed")
    if not outage_matches(old_outage, outage):
        errors.append("latest outage does not match the captured outage")
    elif outage["ended_at"] is None:
        errors.append("captured outage is still open")
    if not status_message_rotated(baseline, state["status_message_id"]):
        errors.append("stable Telegram status message was not rotated")
    if state["measurement_count"] <= old_state["measurement_count"]:
        errors.append("no new valid measurements were stored after baseline")
    if health.get("connectivity") != "online":
        errors.append("Shelly connectivity is not online")
    return errors


def verify_payload(args: argparse.Namespace) -> dict[str, object]:
    """Verify recovery using local HTTP endpoints and read-only SQLite."""

    baseline = load_baseline(args.baseline)
    state = current_db_state(args.db)
    health_code, health = fetch_json(args.health_url, args.timeout)
    ready_code, ready = fetch_json(args.ready_url, args.timeout)
    errors = recovery_errors(baseline, state, health)
    if health_code != 200:
        errors.append(f"health endpoint returned HTTP {health_code}")
    if ready_code != 200 or ready.get("ready") is not True:
        errors.append("ready endpoint is not ready")
    return {
        "kind": "recovery_verification",
        "checked_at": time.time(),
        "health": safe_health_payload(health),
        "ready": ready,
        "state": safe_verification_state(state, baseline),
        "status": "pass" if not errors else "fail",
        "errors": errors,
    }


def write_output(payload: dict[str, object], output: Path | None) -> None:
    """Print JSON and optionally save the same payload to a file."""

    text = json.dumps(payload, indent=2, sort_keys=True)
    print(text)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")


def parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""

    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--db", type=Path, default=DEFAULT_DB)
    cli.add_argument("--health-url", default=DEFAULT_HEALTH)
    cli.add_argument("--ready-url", default=DEFAULT_READY)
    cli.add_argument("--timeout", type=float, default=5.0)
    sub = cli.add_subparsers(dest="command", required=True)
    base = sub.add_parser("baseline", help="capture the current open outage")
    base.add_argument("--output", type=Path)
    verify = sub.add_parser(
        "verify", help="verify recovery against a baseline"
    )
    verify.add_argument("--baseline", type=Path, required=True)
    verify.add_argument("--output", type=Path)
    return cli


def main() -> int:
    """Run baseline or verification and return a stable exit code."""

    args = parser().parse_args()
    if args.timeout <= 0:
        print("ERROR: --timeout must be > 0", file=sys.stderr)
        return 2
    try:
        payload = (
            baseline_payload(args)
            if args.command == "baseline"
            else verify_payload(args)
        )
    except (ValueError, OSError, sqlite3.Error, urllib.error.URLError) as exc:
        payload = {"status": "fail", "error_type": exc.__class__.__name__}
    write_output(payload, args.output)
    return 0 if payload.get("status") == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
