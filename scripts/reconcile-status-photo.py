#!/usr/bin/env python3
"""Reconcile a confirmed current Telegram photo after settled recovery."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import stat
import subprocess
import sys
import time
from contextlib import closing, contextmanager
from pathlib import Path

import github_update as update

LIVE_DB = Path("/var/lib/shelly-voltage/voltage.sqlite3")
LOCK = Path("/run/lock/shelly-voltage-updater.lock")
FENCE_UNITS = (
    "shelly-voltage-compose.service",
    "shelly-voltage-supervised.service",
    "shelly-incident-photo-refresh.timer",
    "shelly-incident-photo-refresh.service",
)
RETIRE_KEYS = (
    "telegram_graphic_publish_pending",
    "telegram_recovery_rotation_pending_at",
    "last_voltage_event",
    "voltage_episode",
    "voltage_recovery_stable_since",
    "voltage_status_edit_window_started_at",
)


def options() -> argparse.Namespace:
    """Read the operator-confirmed photo ID and explicit operating mode."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=LIVE_DB)
    parser.add_argument("--confirmed-photo", required=True)
    parser.add_argument("--phases", nargs="+", choices=update.PHASE_ORDER)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--inspect", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--expect-fingerprint")
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()
    if args.phases is None or "L1" not in args.phases:
        parser.error("--phases must include L1 and all configured phases")
    if not re.fullmatch(r"[1-9][0-9]{0,18}", args.confirmed_photo):
        parser.error("--confirmed-photo must be a numeric message ID")
    if args.apply and (
        args.backup is None
        or not re.fullmatch(r"[a-f0-9]{64}", args.expect_fingerprint or "")
    ):
        parser.error(
            "--apply requires a backup path and inspection fingerprint"
        )
    return args


def validate_paths(database: Path, backup: Path | None = None) -> None:
    """Reject linked databases and require a new private rescue location."""
    paths = (database, *database.parents)
    if not database.is_absolute() or any(path.is_symlink() for path in paths):
        raise ValueError("database path is not absolute and link-free")
    info = database.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("database is not an ordinary single-link file")
    if backup is None:
        return
    if not backup.is_absolute() or any(
        path.is_symlink() for path in backup.parents
    ):
        raise ValueError("backup path is not absolute and link-free")
    parent = backup.parent.stat()
    if parent.st_uid != os.geteuid() or stat.S_IMODE(parent.st_mode) != 0o700:
        raise ValueError("backup directory must be privately owned")


def read_meta(db: sqlite3.Connection) -> dict:
    """Read the recovery state and exact photo identity within one snapshot."""
    keys = (
        *RETIRE_KEYS,
        "telegram_status_message_id",
        "telegram_status_media_identity",
        "telegram_legacy_text_confirm_pending",
        *(f"voltage_detector_{phase}" for phase in update.PHASE_ORDER),
    )
    placeholders = ",".join("?" for _ in keys)
    return dict(
        db.execute(
            f"SELECT key,value FROM meta WHERE key IN ({placeholders})", keys
        )
    )


def verify_photo(meta: dict, expected: str, now: float) -> None:
    """Accept only an existing bound photo and an intent for that photo."""
    if meta.get("telegram_status_message_id") != expected:
        raise ValueError(
            "the stored status ID differs from the confirmed photo"
        )
    marker = str(meta.get("telegram_status_media_identity", ""))
    fields = marker.split(":")
    if (
        len(fields) != 3
        or fields[0] != "photo"
        or not re.fullmatch(r"-?[0-9]+", fields[1])
        or fields[2] != expected
    ):
        raise ValueError("the message has no matching photo identity")
    pending = meta.get("telegram_graphic_publish_pending")
    if pending != f"{fields[1]}:{expected}":
        raise ValueError("the pending send does not match the confirmed photo")
    if "telegram_legacy_text_confirm_pending" in meta:
        raise ValueError("a separate text confirmation is pending")
    if (
        update.timestamp(meta.get("telegram_recovery_rotation_pending_at"))
        > now
    ):
        raise ValueError("the pending rotation timestamp is in the future")


def inspect(
    db: sqlite3.Connection, expected: str, phases: tuple[str, ...]
) -> str:
    """Return a fingerprint when all electrical and photo checks pass."""
    if db.execute("PRAGMA quick_check").fetchone() != ("ok",):
        raise ValueError("SQLite integrity check failed")
    if db.execute(
        "SELECT 1 FROM outages WHERE ended_at IS NULL LIMIT 1"
    ).fetchone():
        raise ValueError("an outage is active")
    meta = read_meta(db)
    detected = tuple(
        phase
        for phase in update.PHASE_ORDER
        if f"voltage_detector_{phase}" in meta
    )
    if detected != phases:
        raise ValueError("confirmed phases differ from the stored detectors")
    now = time.time()
    verify_photo(meta, expected, now)
    if not meta.get("voltage_episode") or not meta.get(
        "voltage_recovery_stable_since"
    ):
        raise ValueError("settled recovery evidence is missing")
    update.verify_recovery(meta, phases, now)
    update.verify_voltage_samples(db, phases, now)
    encoded = json.dumps((phases, meta), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def output_of(*command: str) -> str:
    """Query a named service or container with bounded output and time."""
    result = subprocess.run(
        command, capture_output=True, text=True, timeout=10, check=True
    )
    return result.stdout.strip()


def require_stopped_writers() -> None:
    """Check the main runtime and the known independent photo refresh unit."""
    for unit in FENCE_UNITS:
        if (
            output_of(
                "systemctl", "show", "-p", "ActiveState", "--value", unit
            )
            != "inactive"
        ):
            raise ValueError(
                "a monitor or temporary photo editor is still active"
            )
    state = output_of(
        "docker",
        "inspect",
        "--format",
        "{{.State.Status}}|{{.HostConfig.RestartPolicy.Name}}",
        "shelly-voltage-bot",
    )
    if state != "exited|no":
        raise ValueError("the monitor container is not stopped and fenced")


@contextmanager
def updater_lock():
    """Hold the common root-owned updater lock during repair."""
    fd = os.open(LOCK, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "rb+") as lock:
        info = os.fstat(lock.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != 0
            or info.st_nlink != 1
        ):
            raise ValueError("updater lock is not root-controlled")
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def save_backup(
    source: Path,
    backup: Path,
    fingerprint: str,
    photo: str,
    phases: tuple[str, ...],
) -> None:
    """Copy committed SQLite and verify that the repair inputs match."""
    fd = os.open(
        backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600
    )
    os.close(fd)
    with (
        closing(
            sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=5)
        ) as db,
        closing(sqlite3.connect(backup, timeout=5)) as saved,
    ):
        db.backup(saved)
        if inspect(saved, photo, phases) != fingerprint:
            raise ValueError("the backup does not match the inspected state")
    with backup.open("rb") as stream:
        os.fsync(stream.fileno())
    fd = os.open(backup.parent, os.O_DIRECTORY | os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def commit_repair(args: argparse.Namespace, phases: tuple[str, ...]) -> str:
    """Commit removal of transient keys after verifying the rescue copy."""
    with closing(sqlite3.connect(args.db, timeout=5)) as db, db:
        require_stopped_writers()
        db.execute("PRAGMA synchronous=FULL")
        db.execute("BEGIN IMMEDIATE")
        fingerprint = inspect(db, args.confirmed_photo, phases)
        if fingerprint != args.expect_fingerprint:
            raise ValueError("recovery state changed since inspection")
        save_backup(
            args.db, args.backup, fingerprint, args.confirmed_photo, phases
        )
        require_stopped_writers()
        db.executemany(
            "DELETE FROM meta WHERE key = ?", ((key,) for key in RETIRE_KEYS)
        )
    return fingerprint


def apply_repair(args: argparse.Namespace, phases: tuple[str, ...]) -> str:
    """Restrict the write path to the stopped, locked production monitor."""
    if os.geteuid() != 0 or args.db != LIVE_DB:
        raise ValueError(
            "apply requires root on the installed monitor database"
        )
    validate_paths(args.db, args.backup)
    with updater_lock():
        return commit_repair(args, phases)


def main() -> int:
    """Report the inspected or repaired state without a Telegram API call."""
    args = options()
    phases = tuple(
        phase for phase in update.PHASE_ORDER if phase in args.phases
    )
    try:
        if args.apply:
            fingerprint = apply_repair(args, phases)
        else:
            validate_paths(args.db)
            uri = args.db.as_uri() + "?mode=ro"
            with closing(sqlite3.connect(uri, uri=True, timeout=5)) as db:
                db.execute("BEGIN")
                fingerprint = inspect(db, args.confirmed_photo, phases)
    except (
        OSError,
        ValueError,
        sqlite3.Error,
        subprocess.SubprocessError,
    ) as exc:
        reason = (
            str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        )
        print(f"STATUS RECONCILIATION BLOCKED: {reason}", file=sys.stderr)
        return 3
    print(
        json.dumps(
            {
                "status": "APPLIED" if args.apply else "INSPECTED",
                "photo": args.confirmed_photo,
                "fingerprint": fingerprint,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
