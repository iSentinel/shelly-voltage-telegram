#!/usr/bin/env python3
"""Read-only boot continuity evidence; never authorize a writer start."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sqlite3
import stat
import subprocess
from contextlib import closing
from pathlib import Path

AUDIT = Path(__file__).with_name("deployment-state-audit.py")
CONTAINER = "shelly-voltage-bot"
IMAGE = "ghcr.io/isentinel/shelly-voltage-telegram"
PENDING_NAME = re.compile(r"\d{8}T\d{6}Z-\d+\.\d+\.\d+-hw\Z")


class Blocked(Exception):
    """An unavailable or untrusted state cannot authorize any startup."""


def audit_module():
    """Reuse established private-file and receipt parsing checks."""
    spec = importlib.util.spec_from_file_location("boot_receipt_audit", AUDIT)
    if spec is None or spec.loader is None:
        raise Blocked("RECEIPT_VALIDATOR_MISSING")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def state_directory(path: Path) -> None:
    """Require owner-private 0700 state without symlink redirection."""
    if (
        not path.is_absolute()
        or path.is_symlink()
        or any(p.is_symlink() for p in path.parents)
    ):
        raise Blocked("STATE_PATH_LINKED")
    try:
        info = path.stat()
    except OSError as exc:
        raise Blocked("STATE_UNAVAILABLE") from exc
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o700
        or info.st_uid != os.geteuid()
    ):
        raise Blocked("STATE_DIRECTORY_UNSAFE")


def pending_marker(state: Path, audit) -> bool:
    """Recognize but never clear or rewrite an interrupted update."""
    path = state / "pending-update"
    if path.is_symlink():
        raise Blocked("PENDING_MARKER_LINKED")
    if not path.exists():
        return False
    try:
        token = audit.read_small(path, limit=128, private=True).strip()
    except audit.AuditBlocked as exc:
        raise Blocked("PENDING_MARKER_UNTRUSTED") from exc
    if not PENDING_NAME.fullmatch(token):
        raise Blocked("PENDING_MARKER_INVALID")
    return True


def source_and_receipt(root: Path, state: Path) -> dict:
    """Compare source, immutable override and deployed receipt read-only."""
    audit = audit_module()
    try:
        source = audit.version_marker(root)
        deployed, digest = audit.deployed_record(state)
        override = audit.override_image(state)
    except audit.AuditBlocked as exc:
        raise Blocked("DEPLOYMENT_RECEIPT_INVALID") from exc
    expected = f"{IMAGE}@{digest}"
    if override != expected:
        raise Blocked("PIN_AND_RECEIPT_MISMATCH")
    return {
        "installed_version": source,
        "deployed_version": deployed,
        "image_ref": expected,
        "pending": pending_marker(state, audit),
    }


def database_state(database: Path) -> dict:
    """Verify current SQLite without making a copy or changing its WAL."""
    if (
        not database.is_absolute()
        or database.is_symlink()
        or any(path.is_symlink() for path in database.parents)
        or not database.is_file()
    ):
        raise Blocked("CURRENT_DATABASE_UNSAFE")
    if database.stat().st_nlink != 1:
        raise Blocked("CURRENT_DATABASE_LINKED")
    try:
        with closing(
            sqlite3.connect(
                database.as_uri() + "?mode=ro",
                uri=True,
                timeout=4,
            )
        ) as db:
            db.execute("PRAGMA query_only=ON")
            check = db.execute("PRAGMA quick_check").fetchone()[0]
            samples = db.execute(
                "SELECT count(*) FROM measurements"
            ).fetchone()[0]
            open_outages = db.execute(
                "SELECT count(*) FROM outages WHERE ended_at IS NULL"
            ).fetchone()[0]
    except (OSError, sqlite3.Error) as exc:
        raise Blocked("CURRENT_DATABASE_UNREADABLE") from exc
    if check != "ok" or samples < 1:
        raise Blocked("CURRENT_HISTORY_NOT_VERIFIED")
    return {"measurements": samples, "open_outages": open_outages}


def validate_runtime(data: dict) -> dict:
    """Validate runtime identity without trusting a signature flag."""
    if set(data) != {"image", "running", "health", "restart_policy"}:
        raise Blocked("RUNTIME_FIELDS_INVALID")
    if (
        not isinstance(data["image"], str)
        or not isinstance(data["running"], bool)
        or data["health"] not in ("healthy", "unhealthy", "starting", "none")
        or data["restart_policy"] not in ("no", "unless-stopped", "always")
    ):
        raise Blocked("RUNTIME_VALUES_INVALID")
    return data


def fixture_runtime(path: Path) -> dict:
    """Read an explicit small QA fixture, never a real Docker command."""
    audit = audit_module()
    try:
        payload = json.loads(audit.read_small(path, limit=2048))
    except (audit.AuditBlocked, ValueError, TypeError) as exc:
        raise Blocked("RUNTIME_FIXTURE_INVALID") from exc
    if not isinstance(payload, dict):
        raise Blocked("RUNTIME_FIXTURE_INVALID")
    return validate_runtime(payload)


def live_runtime() -> dict:
    """Inspect the fixed real container without starting or stopping it."""
    try:
        result = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{.Config.Image}}|{{.State.Running}}|"
                "{{.State.Health.Status}}|{{.HostConfig.RestartPolicy.Name}}",
                CONTAINER,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Blocked("RUNTIME_INSPECT_UNAVAILABLE") from exc
    parts = result.stdout.strip().split("|")
    if result.returncode or len(parts) != 4:
        raise Blocked("RUNTIME_INSPECT_FAILED")
    if parts[1] not in ("true", "false"):
        raise Blocked("RUNTIME_RUNNING_INVALID")
    return validate_runtime(
        {
            "image": parts[0],
            "running": parts[1] == "true",
            "health": parts[2],
            "restart_policy": parts[3],
        }
    )


def decision(receipt: dict, database: dict, runtime: dict) -> dict:
    """Produce a bounded observation, never a boot or release permit."""
    reasons = []
    if receipt["installed_version"] != receipt["deployed_version"]:
        reasons.append("INSTALLED_SOURCE_IMAGE_VERSION_MISMATCH")
    if receipt["pending"]:
        reasons.append("PENDING_RECOVERY_NEEDS_REVIEW")
    if runtime["image"] != receipt["image_ref"]:
        reasons.append("RUNTIME_IMAGE_PIN_MISMATCH")
    if not runtime["running"]:
        reasons.append("WRITER_STOPPED_BOOT_CONTINUATION_UNAPPROVED")
    elif runtime["health"] != "healthy":
        reasons.append("RUNNING_WRITER_NOT_HEALTHY")
    if database["open_outages"]:
        reasons.append("OPEN_OUTAGE")
    return {
        "status": "REVIEW_REQUIRED" if reasons else "OBSERVED_RUNNING_PINNED",
        "reasons": reasons,
        "deployed_version": receipt["deployed_version"],
        "installed_version": receipt["installed_version"],
        "writer_observed_running": runtime["running"],
        "current_history_rows": database["measurements"],
        "pending_marker_preserved": receipt["pending"],
        "signature_verified_here": False,
        "signed_digest_verification_required": True,
        "all_restart_owners_fenced": False,
        "boot_start_authorized": False,
        "docker_build_allowed": False,
        "source_install_authorized": False,
        "telegram_photo_authorized": False,
        "production_modified": False,
    }


def denied_report(reason: str) -> dict:
    """Keep prohibitions explicit in every blocked outcome."""
    return {
        "status": "BLOCKED",
        "reasons": [reason],
        "signature_verified_here": False,
        "signed_digest_verification_required": True,
        "all_restart_owners_fenced": False,
        "boot_start_authorized": False,
        "docker_build_allowed": False,
        "source_install_authorized": False,
        "telegram_photo_authorized": False,
        "production_modified": False,
    }


def evaluate(root: Path, state: Path, database: Path, runtime: dict) -> dict:
    """Read private evidence once and deny implicit recovery permissions."""
    try:
        state_directory(state)
        receipt = source_and_receipt(root, state)
        db = database_state(database)
        return decision(receipt, db, validate_runtime(runtime))
    except (Blocked, OSError, ValueError) as exc:
        reason = str(exc) if isinstance(exc, Blocked) else "STATE_UNAVAILABLE"
        return denied_report(reason)


def show_report(report: dict, jsonl: bool) -> None:
    """Print bounded reason codes without exposing private identifiers."""
    if jsonl:
        print(json.dumps(report, separators=(",", ":")))
        return
    print("BOOT CONTINUITY DECISION:", report["status"])
    print("BOOT START NOT AUTHORIZED; NO LIVE CHANGES")
    for reason in report.get("reasons", []):
        print(" -", reason)


def main() -> int:
    """Default to inert dry-run; require explicit read-only audit mode."""
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--dry-run", action="store_true")
    selection.add_argument("--live-readonly", action="store_true")
    selection.add_argument("--runtime-fixture", type=Path)
    parser.add_argument(
        "--root", type=Path, default=Path("/opt/shelly-voltage")
    )
    parser.add_argument(
        "--state", type=Path, default=Path("/var/lib/shelly-voltage-updater")
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("/var/lib/shelly-voltage/voltage.sqlite3"),
    )
    parser.add_argument("--jsonl", action="store_true")
    args = parser.parse_args()
    if not args.live_readonly and args.runtime_fixture is None:
        print("DRY-RUN PASS: no files, Docker or systemd inspected")
        return 0
    try:
        runtime = (
            live_runtime()
            if args.live_readonly
            else fixture_runtime(args.runtime_fixture)
        )
        report = evaluate(args.root, args.state, args.database, runtime)
    except Blocked as exc:
        report = denied_report(str(exc))
    show_report(report, args.jsonl)
    return 0 if report["status"] == "OBSERVED_RUNNING_PINNED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
