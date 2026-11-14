#!/usr/bin/env python3
"""Replay boot decision failures through transient, synthetic systemd units."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path

AUDIT = Path(__file__).with_name("boot-continuity-decision-audit.py")
CONFIRM = "DISPOSABLE_BOOT_CONTINUITY_SYSTEMD_REPLAY_ONLY"
SERVICE = re.compile(r"svt-qa-boot-replay-[a-f0-9]{12}\Z")
DIGEST = "sha256:" + "a" * 64
IMAGE = "ghcr.io/isentinel/shelly-voltage-telegram@" + DIGEST
CASES = (
    "clean",
    "clean-again",
    "pending",
    "pending-wal",
    "source-mismatch",
    "missing-pin",
    "corrupt-db",
    "stopped",
)
REASONS = {
    "clean": "BOOT CONTINUITY DECISION: OBSERVED_RUNNING_PINNED",
    "clean-again": "BOOT CONTINUITY DECISION: OBSERVED_RUNNING_PINNED",
    "pending": "PENDING_RECOVERY_NEEDS_REVIEW",
    "pending-wal": "PENDING_RECOVERY_NEEDS_REVIEW",
    "source-mismatch": "INSTALLED_SOURCE_IMAGE_VERSION_MISMATCH",
    "missing-pin": "DEPLOYMENT_RECEIPT_INVALID",
    "corrupt-db": "CURRENT_DATABASE_UNREADABLE",
    "stopped": "WRITER_STOPPED_BOOT_CONTINUATION_UNAPPROVED",
}


class Blocked(Exception):
    """QA prerequisites or test evidence did not meet the expected result."""


def run(arguments: list[str], *, allow_failure: bool = False) -> str:
    """Execute bounded QA argv, no shell interpolation or production target."""
    try:
        done = subprocess.run(
            arguments,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Blocked("QA_COMMAND_FAILED") from exc
    if len(done.stdout) > 8192 or len(done.stderr) > 8192:
        raise Blocked("QA_COMMAND_OUTPUT_TOO_LARGE")
    if done.returncode and not allow_failure:
        raise Blocked("QA_COMMAND_FAILED")
    return done.stdout


def create_source(root: Path, case: str) -> None:
    """Construct synthetic release markers with one intentional mismatch."""
    source = root / "source"
    source.mkdir()
    version = "0.5.15-hw" if case == "source-mismatch" else "0.5.21-hw"
    for name in ("VERSION", "RELEASE_VERSION"):
        (source / name).write_text(version + "\n")


def create_receipt(root: Path, case: str) -> None:
    """Construct a private fake image pin and pending marker."""
    state = root / "state"
    state.mkdir(mode=0o700)
    pinned = state / "override.yaml"
    pinned.write_text(
        "services:\n  shelly-voltage:\n    image: " + IMAGE + "\n"
    )
    deployed = state / "deployed.json"
    deployed.write_text(
        json.dumps(
            {
                "version": "0.5.21-hw",
                "digest": DIGEST,
            }
        )
    )
    pinned.chmod(0o600)
    deployed.chmod(0o600)
    if case == "missing-pin":
        pinned.unlink()
    if case in ("pending", "pending-wal"):
        marker = state / "pending-update"
        marker.write_text("20261114T190000Z-0.5.21-hw\n")
        marker.chmod(0o600)


def create_database(root: Path, case: str) -> None:
    """Create disposable SQLite including optional new WAL history."""
    database = root / "history.sqlite3"
    with closing(sqlite3.connect(database)) as db:
        db.execute("CREATE TABLE measurements(id INTEGER, voltage REAL)")
        db.execute("CREATE TABLE outages(id INTEGER, ended_at REAL)")
        db.executemany(
            "INSERT INTO measurements VALUES(?,?)",
            [(i, 220 + i % 10) for i in range(120)],
        )
        db.commit()
        if case == "pending-wal":
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA wal_autocheckpoint=0")
            db.execute("INSERT INTO measurements VALUES(121,222.2)")
            db.commit()
    if case == "corrupt-db":
        database.write_bytes(b"intentionally invalid SQLite fixture")


def create_fixture(root: Path, case: str) -> None:
    """Build complete QA inputs without any production-path access."""
    if case not in CASES:
        raise Blocked("QA_CASE_UNSUPPORTED")
    root.mkdir(mode=0o700)
    create_source(root, case)
    create_receipt(root, case)
    create_database(root, case)
    runtime = {
        "image": IMAGE,
        "running": case != "stopped",
        "health": "healthy",
        "restart_policy": "unless-stopped",
    }
    (root / "runtime.json").write_text(json.dumps(runtime))


def data_fingerprint(root: Path) -> str:
    """Hash private state and full logical SQLite including WAL."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.name.startswith("history.sqlite3"):
            digest.update(str(path.relative_to(root)).encode("utf-8"))
            digest.update(path.read_bytes())
    database = root / "history.sqlite3"
    try:
        with closing(
            sqlite3.connect(
                database.as_uri() + "?mode=ro", uri=True, timeout=3
            )
        ) as db:
            db.execute("BEGIN")
            for line in db.iterdump():
                digest.update(line.encode("utf-8"))
                digest.update(b"\\n")
            for flag in ("user_version", "application_id"):
                value = db.execute(f"PRAGMA {flag}").fetchone()[0]
                digest.update(f"{flag}={value}".encode("ascii"))
    except sqlite3.Error:
        # The intentionally corrupt fixture must stay byte-identical.
        digest.update(b"CORRUPT_SQLITE_FIXTURE")
        digest.update(database.read_bytes())
    return digest.hexdigest()


def service_name() -> str:
    """Generate a unique QA-only systemd unit name."""
    return "svt-qa-boot-replay-" + secrets.token_hex(6)


def service_command(name: str, root: Path) -> list[str]:
    """Run only the staged read-only Python prestart on synthetic fixtures."""
    if not SERVICE.fullmatch(name) or not AUDIT.is_file():
        raise Blocked("QA_UNIT_OR_AUDIT_UNSAFE")
    args = [
        sys.executable,
        str(AUDIT),
        "--runtime-fixture",
        str(root / "runtime.json"),
        "--root",
        str(root / "source"),
        "--state",
        str(root / "state"),
        "--database",
        str(root / "history.sqlite3"),
    ]
    return [
        "systemd-run",
        "--wait",
        "--collect",
        "--unit",
        name,
        "--property=Type=oneshot",
        "--property=ExecStartPre=" + " ".join(args),
        "/usr/bin/touch",
        str(root / "main-started"),
    ]


def journal_reason(name: str) -> str:
    """Read only the generated QA service's own systemd journal."""
    if not SERVICE.fullmatch(name):
        raise Blocked("QA_JOURNAL_TARGET_UNSAFE")
    return run(
        [
            "journalctl",
            "--unit",
            name + ".service",
            "--no-pager",
            "-n",
            "32",
            "-o",
            "cat",
        ],
        allow_failure=True,
    )


def cleanup_service(name: str) -> None:
    """Stop and reset only a generated transient QA service."""
    if not SERVICE.fullmatch(name):
        raise Blocked("QA_CLEANUP_TARGET_UNSAFE")
    target = name + ".service"
    run(["systemctl", "stop", target], allow_failure=True)
    run(["systemctl", "reset-failed", target], allow_failure=True)


def validate_case(case: str, root: Path, output: str, succeeded: bool) -> None:
    """Check expected ExecStart and journal reason for each boot condition."""
    expected = case in ("clean", "clean-again")
    marker = (root / "main-started").exists()
    if succeeded != expected or marker != expected:
        raise Blocked("QA_PRESTART_EXIT_OR_MAIN_STATE_UNEXPECTED")
    if REASONS[case] not in output:
        raise Blocked("QA_EXPECTED_JOURNAL_REASON_MISSING")
    if (
        case in ("pending", "pending-wal")
        and not (root / "state/pending-update").exists()
    ):
        raise Blocked("QA_PENDING_MARKER_NOT_PRESERVED")


def run_case(base: Path, case: str) -> None:
    """Replay one systemd ExecStartPre, preserving each scratch SQLite."""
    root = base / case
    create_fixture(root, case)
    before = data_fingerprint(root)
    name = service_name()
    try:
        result = subprocess.run(
            service_command(name, root),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        output = journal_reason(name)
        validate_case(case, root, output, result.returncode == 0)
        (root / "main-started").unlink(missing_ok=True)
        if data_fingerprint(root) != before:
            raise Blocked("QA_HISTORY_OR_MARKER_CHANGED")
    finally:
        cleanup_service(name)
    print(f"PASS {case}: expected boot decision; QA history unchanged")


def prerequisites() -> None:
    """Permit real systemd integration only with full explicit QA opt-in."""
    if os.geteuid() != 0:
        raise Blocked("QA_ROOT_REQUIRED")
    for command in ("systemd-run", "systemctl", "journalctl"):
        if shutil.which(command) is None:
            raise Blocked("QA_SYSTEMD_MISSING")
    if not AUDIT.is_file():
        raise Blocked("QA_AUDIT_FILE_MISSING")


def execute() -> None:
    """Run repeated safe and blocked boot decisions, then delete all input."""
    prerequisites()
    with tempfile.TemporaryDirectory(
        prefix="svt-systemd-boot-replay-qa-",
        dir="/var/tmp",
    ) as directory:
        for case in CASES:
            run_case(Path(directory), case)
    print("PASS disposable systemd boot replay matrix; scratch removed")


def main() -> int:
    """Default to inert dry-run; require explicit disposable-only execution."""
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--dry-run", action="store_true")
    modes.add_argument("--run-disposable", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    if not args.run_disposable:
        print("DRY-RUN PASS: no files or systemd commands executed")
        return 0
    if args.confirm != CONFIRM:
        print("FAIL: exact disposable-only confirmation required")
        return 2
    try:
        execute()
    except Blocked as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 3
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"FAIL: {type(exc).__name__}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
