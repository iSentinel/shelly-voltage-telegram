#!/usr/bin/env python3
"""Fail closed if a disposable rollback SQLite archive loses live history."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sqlite3
import stat
import tempfile
import zlib
from contextlib import closing
from pathlib import Path

MARKER = ".disposable-rollback-history-qa"
MARKER_VALUE = "DISPOSABLE_SQLITE_HISTORY_ROLLBACK_QA\n"
DATABASE = "current.sqlite3"
ARCHIVE = "rollback.sqlite3.gz"
MAX_ARCHIVE_BYTES = 4 * 1024 * 1024
MAX_RESTORED_BYTES = 16 * 1024 * 1024


class Blocked(Exception):
    """A deliberately rejected unsafe rollback parity comparison."""


def require_root(root: Path) -> None:
    """Restrict all paths to owner-private marked scratch directories."""
    if not root.is_absolute() or ".." in root.parts:
        raise Blocked("QA_ROOT_UNSAFE")
    if root.parts[:2] == ("/", "tmp"):
        minimum = 3
    elif root.parts[:3] == ("/", "var", "tmp"):
        minimum = 4
    else:
        raise Blocked("PRODUCTION_PATH_FORBIDDEN")
    if len(root.parts) < minimum:
        raise Blocked("QA_ROOT_UNSAFE")
    parent = Path("/")
    for piece in root.parts[1:]:
        parent = parent / piece
        if parent.is_symlink():
            raise Blocked("QA_SYMLINK_PATH")
    info = root.stat()
    if not stat.S_ISDIR(info.st_mode):
        raise Blocked("QA_ROOT_NOT_DIRECTORY")
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise Blocked("QA_ROOT_NOT_PRIVATE")
    marker = root / MARKER
    if marker.is_symlink() or not marker.is_file():
        raise Blocked("QA_MARKER_MISSING")
    if marker.read_text(encoding="ascii") != MARKER_VALUE:
        raise Blocked("QA_MARKER_INVALID")


def require_private_file(path: Path, limit: int) -> None:
    """Reject symlinks, hardlinks, foreign owners and oversized files."""
    if path.is_symlink() or not path.is_file():
        raise Blocked("QA_INPUT_MISSING_OR_LINKED")
    info = path.stat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_size > limit
    ):
        raise Blocked("QA_INPUT_NOT_PRIVATE_OR_TOO_LARGE")


def db_summary(path: Path) -> tuple[str, int]:
    """Hash full logical schema, rows and SQLite header values."""
    require_private_file(path, MAX_RESTORED_BYTES)
    uri = f"{path.as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as db:
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise Blocked("QA_SQLITE_INTEGRITY_FAILED")
        tables = [
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        ]
        if "measurements" not in tables:
            raise Blocked("QA_MEASUREMENTS_TABLE_MISSING")
        rows = db.execute("SELECT count(*) FROM measurements").fetchone()[0]
        digest = hashlib.sha256()
        for line in db.iterdump():
            digest.update(line.encode("utf-8"))
            digest.update(b"\n")
        for key in ("user_version", "application_id"):
            value = db.execute(f"PRAGMA {key}").fetchone()[0]
            digest.update(f"{key}:{value}\n".encode("ascii"))
    return digest.hexdigest(), rows


def inflate_scratch(root: Path) -> Path:
    """Expand only a bounded synthetic backup into an owner-private file."""
    archive = root / ARCHIVE
    require_private_file(archive, MAX_ARCHIVE_BYTES)
    fd, name = tempfile.mkstemp(prefix=".rollback-expanded-", dir=root)
    restored = Path(name)
    try:
        with (
            os.fdopen(fd, "wb") as destination,
            gzip.open(archive, "rb") as source,
        ):
            data = source.read(MAX_RESTORED_BYTES + 1)
            if len(data) > MAX_RESTORED_BYTES:
                raise Blocked("QA_RESTORED_ARCHIVE_TOO_LARGE")
            destination.write(data)
            destination.flush()
            os.fsync(destination.fileno())
        os.chmod(restored, 0o600)
    except (OSError, EOFError, Blocked, zlib.error):
        restored.unlink(missing_ok=True)
        raise
    return restored


def audit(root: Path) -> dict:
    """Compare whole SQLite histories but never authorize rollback."""
    require_root(root)
    current_digest, current_rows = db_summary(root / DATABASE)
    restored = inflate_scratch(root)
    try:
        archived_digest, archived_rows = db_summary(restored)
    finally:
        restored.unlink(missing_ok=True)
    equality = current_digest == archived_digest
    reason = "EXACT_WHOLE_HISTORY_MATCH" if equality else "HISTORY_DIVERGED"
    return {
        "status": "PARITY_PASS" if equality else "BLOCKED",
        "reason": reason,
        "live_rows": current_rows,
        "archive_rows": archived_rows,
        "whole_history_equal": equality,
        "production_modified": False,
        "rollback_authorized": False,
        "public_photo_authorized": False,
    }


def main() -> int:
    """Expose a safe dry-run and explicit disposable comparison only."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--audit-disposable", action="store_true")
    args = parser.parse_args()
    if not args.audit_disposable:
        print("QA ROLLBACK DRY-RUN PASS: no files opened or changed")
        return 0
    try:
        result = audit(args.root)
    except (
        Blocked,
        OSError,
        sqlite3.Error,
        EOFError,
        UnicodeError,
    ) as exc:
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "reason": type(exc).__name__,
                    "production_modified": False,
                    "rollback_authorized": False,
                }
            )
        )
        return 3
    print(json.dumps(result, sort_keys=True))
    return 0 if result["whole_history_equal"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
