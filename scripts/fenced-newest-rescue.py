#!/usr/bin/env python3
"""Capture newest committed SQLite and WAL history under an updater fence."""

from __future__ import annotations

import argparse
import hashlib
import os
import sqlite3
import stat
import sys
import tempfile
from contextlib import closing
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from writer_fence import (  # noqa: E402
    WriterFenceError,
    fence_directory,
    quiescent_writer_lease,
    update_gate,
)


class RescueBlocked(ValueError):
    """Refuse incomplete or unsafe newest-history rescue."""


def arguments() -> argparse.Namespace:
    """Parse explicitly selected input, output, and protected fence paths."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--fence-dir", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def safe_paths(db: Path, out: Path) -> None:
    """Reject linked, relative, exposed or pre-existing output artifacts."""
    if not db.is_absolute() or not out.is_absolute():
        raise RescueBlocked("SVT_RESCUE_PATH_RELATIVE")
    if db.is_symlink() or not db.is_file():
        raise RescueBlocked("SVT_RESCUE_SOURCE_INVALID")
    if db.stat().st_nlink != 1:
        raise RescueBlocked("SVT_RESCUE_SOURCE_LINKED")
    for parent in (*db.parents, *out.parent.parents, out.parent):
        if parent.is_symlink():
            raise RescueBlocked("SVT_RESCUE_PATH_LINKED")
    info = out.parent.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != 0
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise RescueBlocked("SVT_RESCUE_DIR_UNSAFE")
    if out.exists() or out.is_symlink():
        raise RescueBlocked("SVT_RESCUE_OUTPUT_EXISTS")


def require_pending(db: Path) -> None:
    """Validate a root-owned, durable update marker under its gate."""
    with update_gate(db):
        marker = fence_directory(db) / ".svt-update-pending"
        try:
            info = marker.lstat()
        except FileNotFoundError as exc:
            raise RescueBlocked("SVT_RESCUE_PENDING_REQUIRED") from exc
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != 0
            or info.st_nlink != 1
        ):
            raise RescueBlocked("SVT_RESCUE_PENDING_UNSAFE")


def logical_digest(db: sqlite3.Connection) -> str:
    """Hash complete SQLite logical data without exposing its contents."""
    digest = hashlib.sha256()
    for statement in db.iterdump():
        digest.update(statement.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def check_sqlite(db: sqlite3.Connection) -> None:
    """Require valid three-phase history and persisted Telegram identity."""
    if db.execute("PRAGMA quick_check").fetchone() != ("ok",):
        raise RescueBlocked("SVT_RESCUE_SQLITE_INVALID")
    phases = {
        phase
        for (phase,) in db.execute("SELECT DISTINCT phase FROM measurements")
    }
    if not {"L1", "L2", "L3"}.issubset(phases):
        raise RescueBlocked("SVT_RESCUE_MISSING_PHASES")
    model = db.execute(
        "SELECT value FROM meta WHERE key='telegram_setting_meter_type'"
    ).fetchone()
    photo = db.execute(
        "SELECT value FROM meta WHERE key='telegram_status_media_identity'"
    ).fetchone()
    if model != ("pro3em",) or not photo or not photo[0]:
        raise RescueBlocked("SVT_RESCUE_IDENTITY_MISMATCH")


def backup_consistently(source: Path, temp: Path) -> str:
    """Copy committed WAL pages and verify complete logical parity."""
    uri = f"file:{quote(str(source))}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as old:
        with closing(sqlite3.connect(temp)) as rescued:
            old.backup(rescued)
        with closing(sqlite3.connect(temp)) as restored:
            check_sqlite(restored)
            check_sqlite(old)
            original = logical_digest(old)
            if logical_digest(restored) != original:
                raise RescueBlocked("SVT_RESCUE_HISTORY_CHANGED")
    return original


def persist_backup(source: Path, destination: Path) -> str:
    """Stage and fsync a newest-history backup without overwriting a file."""
    fd, name = tempfile.mkstemp(prefix=".svt-rescue-", dir=destination.parent)
    temp = Path(name)
    os.close(fd)
    try:
        result = backup_consistently(source, temp)
        with temp.open("rb") as file:
            os.fsync(file.fileno())
        safe_paths(source, destination)
        os.replace(temp, destination)
        directory = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return result
    finally:
        temp.unlink(missing_ok=True)


def main() -> int:
    """Require a published update and stopped sole owner before any rescue."""
    args = arguments()
    try:
        if os.geteuid() != 0 or not args.fence_dir.is_absolute():
            raise RescueBlocked("SVT_RESCUE_ROOT_REQUIRED")
        os.environ["SVT_WRITER_FENCE_DIR"] = str(args.fence_dir)
        fence_directory(args.database)
        safe_paths(args.database, args.output)
        require_pending(args.database)
        with quiescent_writer_lease(args.database):
            require_pending(args.database)
            if args.dry_run:
                print("DRY-RUN: RESCUE_QUIESCENT_PASS")
                return 0
            digest = persist_backup(args.database, args.output)
    except (
        OSError,
        sqlite3.DatabaseError,
        RescueBlocked,
        WriterFenceError,
    ) as exc:
        code = (
            str(exc)
            if isinstance(exc, (ValueError, WriterFenceError))
            else type(exc).__name__
        )
        print(f"FAIL: {code}", file=sys.stderr)
        return 3
    print("PASS: NEWEST_FULL_WAL_RESCUE", digest[:16])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
