#!/usr/bin/env python3
"""Prevent stale SQLite archive from replacing newer current history."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import os
import sqlite3
import sys
import tempfile
import zlib
from contextlib import closing
from pathlib import Path

MAX_EXPANDED_BYTES = 512 * 1024 * 1024
MAX_COMPRESSED_BYTES = 512 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024


class GuardFailure(Exception):
    """A snapshot cannot be trusted to preserve existing history."""


def require_sqlite_path(path: Path) -> None:
    """Reject missing or linked input paths before reading any data."""
    if path.is_symlink() or not path.is_file():
        raise GuardFailure("MISSING_OR_LINKED_SQLITE")
    if path.stat().st_nlink != 1:
        raise GuardFailure("HARDLINKED_SQLITE")


def sqlite_fingerprint(path: Path) -> str:
    """Hash the complete logical history, schema and relevant header flags."""
    require_sqlite_path(path)
    digest = hashlib.sha256()
    with closing(
        sqlite3.connect(
            path.as_uri() + "?mode=ro",
            uri=True,
            timeout=2,
        )
    ) as db:
        # Keep table and metadata reads in one consistent SQLite snapshot.
        db.execute("BEGIN")
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise GuardFailure("SQLITE_INTEGRITY_FAILED")
        for line in db.iterdump():
            digest.update(line.encode("utf-8"))
            digest.update(b"\n")
        for key in ("user_version", "application_id"):
            value = db.execute(f"PRAGMA {key}").fetchone()[0]
            digest.update(f"{key}={value}\n".encode("ascii"))
    return digest.hexdigest()


def expand_archive(archive: Path, destination: Path) -> None:
    """Expand a checksum-verified archive into a bounded isolated file."""
    if archive.is_symlink() or not archive.is_file():
        raise GuardFailure("MISSING_OR_LINKED_ARCHIVE")
    metadata = archive.stat()
    if metadata.st_nlink != 1 or metadata.st_size > MAX_COMPRESSED_BYTES:
        raise GuardFailure("ARCHIVE_UNSAFE_OR_TOO_LARGE")
    size = 0
    with (
        gzip.open(archive, "rb") as source,
        destination.open("xb") as output,
    ):
        while True:
            block = source.read(CHUNK_BYTES)
            if not block:
                break
            size += len(block)
            if size > MAX_EXPANDED_BYTES:
                raise GuardFailure("ARCHIVE_EXPANSION_LIMIT")
            output.write(block)
        output.flush()
        os.fsync(output.fileno())


def verify_live_parity(database: Path, archive: Path) -> None:
    """Require exact current/archive parity twice, without replacing SQLite."""
    current = sqlite_fingerprint(database)
    with tempfile.TemporaryDirectory(
        prefix=".pending-rollback-qa-",
        dir=archive.parent,
    ) as folder:
        restored = Path(folder) / "archived.sqlite3"
        expand_archive(archive, restored)
        expected = sqlite_fingerprint(restored)
    if current != expected:
        raise GuardFailure("STALE_ARCHIVE_OR_CHANGED_HISTORY")
    if sqlite_fingerprint(database) != expected:
        raise GuardFailure("LIVE_HISTORY_CHANGED_DURING_CHECK")


def main() -> int:
    """Check read-only parity; never install, restore or authorize anything."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    arguments = parser.parse_args()
    if (
        not arguments.database.is_absolute()
        or not arguments.archive.is_absolute()
    ):
        print(
            "HISTORY_GUARD BLOCKED: absolute paths required", file=sys.stderr
        )
        return 3
    try:
        verify_live_parity(arguments.database, arguments.archive)
    except (
        GuardFailure,
        sqlite3.Error,
        OSError,
        EOFError,
        UnicodeError,
        zlib.error,
    ) as exc:
        print(f"HISTORY_GUARD BLOCKED: {type(exc).__name__}", file=sys.stderr)
        return 3
    print("HISTORY_GUARD PASS: exact SQLite parity; live database preserved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
