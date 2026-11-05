#!/usr/bin/env python3
"""Capture and verify a read-only mains-recovery acceptance checkpoint."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
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
STABLE_RECOVERY_SECONDS = 15 * 60


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


def meta_value(db: sqlite3.Connection, key: str) -> object:
    """Return one metadata value from the read-only database."""

    return scalar(db, "SELECT value FROM meta WHERE key=?", (key,))


def stored_event_kind(raw: object) -> str | None:
    """Return the persisted voltage event kind without exposing details."""

    if not raw:
        return None
    try:
        payload = json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    kind = payload.get("kind") if isinstance(payload, dict) else None
    return str(kind) if kind else None


def current_db_state(path: Path) -> dict[str, object]:
    """Read recovery-relevant database state without modifying it."""

    with closing(open_db(path)) as db:
        outage = db.execute(
            "SELECT id,started_at,ended_at,phase FROM outages "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        episode = meta_value(db, "voltage_episode")
        return {
            "quick_check": scalar(db, "PRAGMA quick_check"),
            "zero_voltage_rows": scalar(
                db, "SELECT COUNT(*) FROM measurements WHERE voltage=0"
            ),
            "measurement_count": scalar(
                db, "SELECT COUNT(*) FROM measurements"
            ),
            "status_message_id": meta_value(db, "telegram_status_message_id"),
            "recovery_stable_since": meta_value(
                db, "voltage_recovery_stable_since"
            ),
            "last_notification_at": meta_value(
                db, "last_voltage_notification_at"
            ),
            "last_voltage_event_kind": stored_event_kind(
                meta_value(db, "last_voltage_event")
            ),
            "voltage_episode_present": bool(episode),
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
    if not isinstance(payload, dict):
        raise ValueError("recovery baseline must be a JSON object")
    if payload.get("kind") != "recovery_baseline":
        raise ValueError("file is not a recovery baseline")
    if payload.get("status") != "pass":
        raise ValueError("recovery baseline was not accepted at capture")
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


def safe_stable_state(state: dict[str, object]) -> dict[str, object]:
    """Return voltage-recovery state without the raw Telegram message ID."""

    safe = dict(state)
    message_id = safe.pop("status_message_id", None)
    safe["status_message_present"] = bool(message_id)
    safe["status_message_fingerprint"] = status_message_fingerprint(message_id)
    return safe


def stable_since_value(state: dict[str, object]) -> float | None:
    """Parse the persisted stable-recovery timestamp."""

    raw = state.get("recovery_stable_since")
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) and value > 0 else None


def stable_baseline_errors(
    state: dict[str, object],
    health: dict[str, object],
    ready: dict[str, object],
) -> list[str]:
    """Validate a live voltage-recovery timer baseline."""

    errors = []
    stable_since = stable_since_value(state)
    if state["quick_check"] != "ok" or state["zero_voltage_rows"] != 0:
        errors.append("database integrity or zero-voltage check failed")
    if not state["status_message_id"]:
        errors.append("stable Telegram status message ID is missing")
    if stable_since is None:
        errors.append("stable recovery timer is not active")
    if state["last_voltage_event_kind"] != "recovered":
        errors.append("latest voltage event is not recovered")
    if health.get("connectivity") != "online":
        errors.append("Shelly connectivity is not online")
    if ready.get("ready") is not True:
        errors.append("ready endpoint is not ready")
    return errors


def stable_baseline_payload(args: argparse.Namespace) -> dict[str, object]:
    """Capture read-only state while the 15-minute recovery timer is active."""

    state = current_db_state(args.db)
    health_code, health = fetch_json(args.health_url, args.timeout)
    ready_code, ready = fetch_json(args.ready_url, args.timeout)
    errors = stable_baseline_errors(state, health, ready)
    if health_code != 200:
        errors.append(f"health endpoint returned HTTP {health_code}")
    if ready_code != 200:
        errors.append(f"ready endpoint returned HTTP {ready_code}")
    return {
        "kind": "stable_recovery_baseline",
        "created_at": time.time(),
        "database": str(args.db),
        "state": safe_stable_state(state),
        "status": "pass" if not errors else "fail",
        "errors": errors,
    }


def load_stable_baseline(path: Path) -> dict[str, object]:
    """Load one stable-recovery baseline."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read baseline: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("stable baseline must be a JSON object")
    if payload.get("kind") != "stable_recovery_baseline":
        raise ValueError("file is not a stable recovery baseline")
    if payload.get("status") != "pass":
        raise ValueError("stable baseline was not accepted at capture")
    if not isinstance(payload.get("state"), dict):
        raise ValueError("stable recovery baseline has no state")
    return payload


def stable_status_rotated(
    baseline: dict[str, object], current_id: object
) -> bool:
    """Compare current status ID with a stable-recovery fingerprint."""

    old = str(baseline["state"].get("status_message_fingerprint") or "")
    current = status_message_fingerprint(current_id)
    return bool(old and current and old != current)


def stable_outage_unchanged(
    old: dict[str, object], state: dict[str, object]
) -> bool:
    """Reject a stable baseline spanning another connectivity outage."""

    return "outage" in old and old["outage"] == state.get("outage")


def stable_verify_errors(
    baseline: dict[str, object],
    state: dict[str, object],
    health: dict[str, object],
    ready: dict[str, object],
    now: float,
) -> list[str]:
    """Validate the completed silent post-recovery rotation."""

    errors = []
    old = baseline["state"]
    started = stable_since_value(old)
    if started is None or now - started < STABLE_RECOVERY_SECONDS:
        errors.append("15-minute stable recovery period has not elapsed")
    if not stable_outage_unchanged(old, state):
        errors.append("Shelly outage changed since stable baseline")
    if not stable_status_rotated(baseline, state["status_message_id"]):
        errors.append("recovery status message was not rotated")
    if state["recovery_stable_since"] is not None:
        errors.append("stable recovery timer was not cleared")
    if state["last_voltage_event_kind"] is not None:
        errors.append("recovery event context was not cleared")
    if state["voltage_episode_present"]:
        errors.append("voltage episode context was not cleared")
    if state["last_notification_at"] != old.get("last_notification_at"):
        errors.append("audible notification cooldown timestamp changed")
    if state["measurement_count"] <= old.get("measurement_count", 0):
        errors.append("no new valid measurements were stored")
    if state["quick_check"] != "ok" or state["zero_voltage_rows"] != 0:
        errors.append("database integrity or zero-voltage check failed")
    if (
        health.get("connectivity") != "online"
        or ready.get("ready") is not True
    ):
        errors.append("service is not online and ready")
    return errors


def stable_verify_payload(args: argparse.Namespace) -> dict[str, object]:
    """Verify the 15-minute silent recovery-status replacement."""

    baseline = load_stable_baseline(args.baseline)
    state = current_db_state(args.db)
    health_code, health = fetch_json(args.health_url, args.timeout)
    ready_code, ready = fetch_json(args.ready_url, args.timeout)
    now = time.time()
    errors = stable_verify_errors(baseline, state, health, ready, now)
    if health_code != 200:
        errors.append(f"health endpoint returned HTTP {health_code}")
    if ready_code != 200:
        errors.append(f"ready endpoint returned HTTP {ready_code}")
    safe = safe_stable_state(state)
    safe["status_message_rotated"] = stable_status_rotated(
        baseline, state["status_message_id"]
    )
    return {
        "kind": "stable_recovery_verification",
        "checked_at": now,
        "read_only": True,
        "health": safe_health_payload(health),
        "ready": ready,
        "state": safe,
        "status": "pass" if not errors else "fail",
        "errors": errors,
    }


def checkpoint_name(payload: dict[str, object]) -> str:
    """Name a checkpoint using the actual event, not the current date."""

    state = payload["state"]
    if payload["kind"] == "recovery_baseline":
        outage = state["outage"]
        return (
            f"outage-{int(outage['id'])}-"
            f"{int(float(outage['started_at']) * 1000)}.json"
        )
    started = stable_since_value(state)
    if started is None:
        raise ValueError("stable checkpoint has no start timestamp")
    return f"stable-{int(started * 1000)}.json"


def write_private_checkpoint(path: Path, payload: dict[str, object]) -> bool:
    """Write once using a private directory and atomic exclusive creation."""

    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.parent.stat().st_mode & 0o077:
        raise ValueError("checkpoint directory must have mode 0700")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        return False
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return True


def capture_once_payload(args: argparse.Namespace) -> dict[str, object]:
    """Capture real outage or voltage-recovery context once, without RPC."""

    state = current_db_state(args.db)
    outage = state["outage"]
    if outage is not None and outage["ended_at"] is None:
        payload = baseline_payload(args)
    elif stable_since_value(state) is not None:
        payload = stable_baseline_payload(args)
    else:
        return {"kind": "natural_checkpoint", "status": "skip"}
    if payload["status"] != "pass":
        return payload
    path = args.output_dir / checkpoint_name(payload)
    created = write_private_checkpoint(path, payload)
    return {
        "kind": "natural_checkpoint",
        "status": "pass",
        "path": str(path),
        "created": created,
        "baseline_kind": payload["kind"],
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
    stable_base = sub.add_parser(
        "stable-baseline",
        help="capture an active 15-minute voltage recovery timer",
    )
    stable_base.add_argument("--output", type=Path)
    stable_verify = sub.add_parser(
        "stable-verify",
        help="verify silent rotation after 15 stable minutes",
    )
    stable_verify.add_argument("--baseline", type=Path, required=True)
    stable_verify.add_argument("--output", type=Path)
    capture = sub.add_parser(
        "capture-once", help="save natural event baseline if one is active"
    )
    capture.add_argument(
        "--output-dir",
        type=Path,
        default=Path("validation/natural-baselines"),
    )
    return cli


def main() -> int:
    """Run baseline or verification and return a stable exit code."""

    args = parser().parse_args()
    if args.timeout <= 0:
        print("ERROR: --timeout must be > 0", file=sys.stderr)
        return 2
    try:
        handlers = {
            "baseline": baseline_payload,
            "verify": verify_payload,
            "stable-baseline": stable_baseline_payload,
            "stable-verify": stable_verify_payload,
            "capture-once": capture_once_payload,
        }
        payload = handlers[args.command](args)
    except (ValueError, OSError, sqlite3.Error, urllib.error.URLError) as exc:
        payload = {"status": "fail", "error_type": exc.__class__.__name__}
    write_output(payload, getattr(args, "output", None))
    return 0 if payload.get("status") in {"pass", "skip"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
