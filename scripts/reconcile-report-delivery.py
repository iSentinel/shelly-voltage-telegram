#!/usr/bin/env python3
"""Inspect report intents and reconcile confirmed QA outcomes."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import socket
import sqlite3
import stat
import sys
from contextlib import closing, contextmanager
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from writer_fence import (  # noqa: E402
    WriterFenceError,
    fence_directory,
    transaction_gate,
    writer_lease,
)

PREFIX = "telegram_report_pending:"
DAILY = "daily_summary_day"
WEEKLY = re.compile(r"telegram_weekly_private_sent_[1-9][0-9]{0,19}")
QA_ROOT = Path("/var/tmp")
QA_PROTECTED = Path("/var/lib/svt-report-reconcile-qa")
LOCK_NAMES = ("signed-forward.lock", "updater.lock")


def valid_period(key: str, period: str) -> bool:
    """Accept only canonical report periods and supported stream keys."""
    try:
        if key == DAILY:
            return date.fromisoformat(period).isoformat() == period
        if WEEKLY.fullmatch(key) and re.fullmatch(
            r"[0-9]{4}-W[0-9]{2}", period
        ):
            year, week = period.split("-W")
            day = date.fromisocalendar(int(year), int(week), 1)
            return (
                f"{day.isocalendar().year:04d}-W{day.isocalendar().week:02d}"
                == period
            )
    except (TypeError, ValueError):
        pass
    return False


def database_identity(path: Path) -> tuple[int, int]:
    """Reject links and bind inspection to the actual database inode."""
    if not path.is_absolute() or any(
        p.is_symlink() for p in (path, *path.parents)
    ):
        raise ValueError("database path must be absolute and link-free")
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("database must be an ordinary single-link file")
    return info.st_dev, info.st_ino


def read_state(
    db: sqlite3.Connection, key: str
) -> tuple[str, str | None, str]:
    """Require an unresolved canonical period and no newer delivery."""
    if db.execute("PRAGMA quick_check").fetchone() != ("ok",):
        raise ValueError("SQLite integrity check failed")
    rows = dict(
        db.execute(
            "SELECT key,value FROM meta WHERE key IN (?,?)",
            (PREFIX + key, key),
        )
    )
    raw = rows.get(PREFIX + key)
    previous = rows.get(key)
    if not isinstance(raw, str):
        raise ValueError("missing pending report")
    period, separator, attempt = raw.partition(":")
    if not separator or not re.fullmatch(r"[a-f0-9]{32}", attempt):
        raise ValueError("legacy or invalid reservation identity")
    if not valid_period(key, period):
        raise ValueError("malformed pending report period")
    if previous is not None and (
        not isinstance(previous, str)
        or not valid_period(key, previous)
        or previous >= period
    ):
        raise ValueError("delivered period conflicts with pending report")
    return period, previous, attempt


def fingerprint(path: Path, key: str, state: tuple) -> str:
    """Bind the reviewed stream state to the database identity."""
    data = (database_identity(path), key, state)
    return hashlib.sha256(
        json.dumps(data, separators=(",", ":")).encode()
    ).hexdigest()


def inspect(path: Path, key: str) -> dict:
    """Return read-only evidence without contacting Telegram."""
    database_identity(path)
    with closing(
        sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
    ) as db:
        db.execute("BEGIN")
        state = read_state(db, key)
    return {
        "status": "PENDING_REVIEW",
        "stream": key,
        "period": state[0],
        "attempt_id": state[2],
        "fingerprint": fingerprint(path, key, state),
        "delivery_verified": False,
        "deployment_authorized": False,
    }


def require_qa(path: Path, backup: Path, protected: bool = False) -> None:
    """Allow writes only on the isolated QA host and private temp paths."""
    if os.geteuid() != 0 or socket.gethostname() != "shelly-docker-reboot-qa":
        raise ValueError("apply is restricted to the isolated QA host")
    configured = os.environ.get("SVT_WRITER_FENCE_DIR")
    expected = str(QA_PROTECTED / "fence") if protected else None
    if (configured or None) != expected:
        raise ValueError("unexpected QA writer fence")
    for item in (path, backup):
        if (
            not item.is_absolute()
            or QA_ROOT not in item.parents
            or any(p.is_symlink() for p in (item, *item.parents))
        ):
            raise ValueError("QA paths must be link-free under /var/tmp")
    info = backup.parent.stat()
    if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError("backup parent must be root-owned mode 0700")
    database_identity(path)


def protected_info(path: Path, *, directory: bool, uid: int = 0):
    """Verify an ordinary protected inode without following links."""
    for parent in path.parents:
        info = parent.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != 0
            or info.st_mode & 0o022
        ):
            raise ValueError("unsafe protected ancestor")
    info = path.lstat()
    valid = (
        stat.S_ISDIR(info.st_mode)
        if directory
        else (stat.S_ISREG(info.st_mode) and info.st_nlink == 1)
    )
    if not valid or info.st_uid != uid or info.st_mode & 0o022:
        raise ValueError("unsafe protected inode")
    return info


@contextmanager
def operator_lock(path: Path):
    """Acquire an existing root-owned operator lock without replacing it."""
    expected = protected_info(path, directory=False)
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if (info.st_dev, info.st_ino) != (expected.st_dev, expected.st_ino):
            raise ValueError("operator lock changed")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def no_update_pending(root: Path) -> None:
    """Reject both kinds of pending update, including dangling links."""
    for marker in (
        root / "pending-update",
        root / "fence/.svt-update-pending",
    ):
        try:
            marker.lstat()
        except FileNotFoundError:
            continue
        raise ValueError("update pending")


@contextmanager
def protected_qa_gate(path: Path):
    """Mirror the production lock order on a dedicated QA-only layout."""
    root = QA_PROTECTED
    protected_info(root, directory=True)
    directory = fence_directory(path)
    if directory != root / "fence":
        raise ValueError("unexpected protected fence directory")
    for name in (".svt-owner.lock", ".svt-transaction.lock"):
        protected_info(directory / name, directory=False, uid=10001)
    with (
        operator_lock(root / LOCK_NAMES[0]),
        operator_lock(root / LOCK_NAMES[1]),
    ):
        no_update_pending(root)
        yield
        no_update_pending(root)


def save_backup(path: Path, backup: Path, key: str, state: tuple) -> str:
    """Create a standalone rescue before changing a pending reservation."""
    fd = os.open(
        backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600
    )
    os.close(fd)
    with (
        closing(
            sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
        ) as source,
        closing(sqlite3.connect(backup, timeout=5)) as saved,
    ):
        source.backup(saved)
        if read_state(saved, key) != state:
            raise ValueError("backup state differs from reviewed report")
    with backup.open("rb") as stream:
        os.fsync(stream.fileno())
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    fd = os.open(backup.parent, os.O_DIRECTORY | os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return digest


def settle(
    db: sqlite3.Connection, key: str, state: tuple, args, digest: str
) -> None:
    """Atomically record operator evidence and settle the exact reservation."""
    period, _, attempt = state
    if args.outcome == "delivered":
        db.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, period),
        )
    record = json.dumps(
        {
            "period": period,
            "attempt_id": attempt,
            "outcome": args.outcome,
            "evidence_ref": args.evidence_ref,
            "message_id": args.message_id,
            "backup_sha256": digest,
            "operator_attested": True,
        },
        sort_keys=True,
    )
    audit_key = f"telegram_report_resolution:{key}:{attempt}"
    db.execute("INSERT INTO meta(key,value) VALUES(?,?)", (audit_key, record))
    cursor = db.execute(
        "DELETE FROM meta WHERE key=? AND value=?",
        (PREFIX + key, f"{period}:{attempt}"),
    )
    if cursor.rowcount != 1:
        raise ValueError("pending report changed")


def apply(args: argparse.Namespace) -> dict:
    """Keep the writer excluded and validate the exact state again."""
    protected = getattr(args, "protected_qa", False)
    require_qa(args.database, args.backup, protected)
    if protected:
        with protected_qa_gate(args.database):
            return apply_locked(args)
    return apply_locked(args)


def apply_locked(args: argparse.Namespace) -> dict:
    """Settle under the writer and transaction gates, after a rescue."""
    with writer_lease(args.database), transaction_gate(args.database):
        current = inspect(args.database, args.stream)
        if current["fingerprint"] != args.expect_fingerprint:
            raise ValueError("inspection fingerprint changed")
        with closing(
            sqlite3.connect(args.database.as_uri() + "?mode=ro", uri=True)
        ) as source:
            state = read_state(source, args.stream)
        digest = save_backup(args.database, args.backup, args.stream, state)
        with (
            closing(
                sqlite3.connect(
                    args.database.as_uri() + "?mode=rw", uri=True, timeout=5
                )
            ) as db,
            db,
        ):
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            if (
                read_state(db, args.stream) != state
                or fingerprint(args.database, args.stream, state)
                != args.expect_fingerprint
            ):
                raise ValueError("reviewed state changed")
            settle(db, args.stream, state, args, digest)
    return {
        "status": "PASS_QA_OPERATOR_RECONCILIATION_ONLY",
        "outcome": args.outcome,
        "backup_sha256": digest,
        "operator_attested": True,
        "remote_delivery_independently_verified": False,
        "deployment_authorized": False,
    }


def options() -> argparse.Namespace:
    """Require explicit review, outcome and rescue path for writes."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--stream", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--inspect", action="store_true")
    mode.add_argument("--apply-qa", action="store_true")
    parser.add_argument("--protected-qa", action="store_true")
    parser.add_argument("--expect-fingerprint")
    parser.add_argument("--outcome", choices=("delivered", "rejected"))
    parser.add_argument("--evidence-ref")
    parser.add_argument("--message-id")
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()
    if args.protected_qa and not args.apply_qa:
        parser.error("--protected-qa requires --apply-qa")
    if args.stream != DAILY and not WEEKLY.fullmatch(args.stream):
        parser.error("unsupported report stream")
    if args.apply_qa and (
        not re.fullmatch(r"[a-f0-9]{64}", args.expect_fingerprint or "")
        or not args.outcome
        or not args.backup
        or not re.fullmatch(r"[A-Za-z0-9._:-]{1,100}", args.evidence_ref or "")
        or (
            args.outcome == "delivered"
            and not re.fullmatch(r"[1-9][0-9]{0,18}", args.message_id or "")
        )
        or (args.outcome == "rejected" and args.message_id is not None)
    ):
        parser.error(
            "apply needs fingerprint, evidence, outcome and backup; "
            "delivered also needs message ID"
        )
    return args


def main() -> int:
    """Report a non-authorizing result or fail closed."""
    args = options()
    try:
        report = (
            inspect(args.database, args.stream)
            if args.inspect
            else apply(args)
        )
    except (OSError, ValueError, sqlite3.Error, WriterFenceError) as exc:
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "reason": type(exc).__name__,
                    "deployment_authorized": False,
                }
            )
        )
        return 3
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
