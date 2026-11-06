#!/usr/bin/env python3
"""Inspect a rollback snapshot without changing the installed service."""

from __future__ import annotations

import argparse
import gzip
import sqlite3
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

BLOCK_SIZE = 1024 * 1024


def parse_args() -> argparse.Namespace:
    """Accept the snapshot folder and the maximum restored database size."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--max-size-mib", type=int, default=2048)
    return parser.parse_args()


def check_source_archive(source: Path) -> None:
    """Reject missing source metadata, credentials, and unsafe tar entries."""

    version_found = False
    with tarfile.open(source, "r:gz") as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            parts = path.parts
            if path.is_absolute() or ".." in parts:
                raise ValueError("unsafe source archive path")
            if member.issym() or member.islnk() or member.isdev():
                raise ValueError(
                    "links and devices are not valid source files"
                )
            if "secrets" in parts or any(
                item.startswith(".env") and item != ".env.example"
                for item in parts
            ):
                raise ValueError("private configuration in source archive")
            if path.as_posix() == "VERSION" and member.isfile():
                version_found = True
    if not version_found:
        raise ValueError("source archive missing VERSION")


def restore_bounded(archive: Path, target: Path, max_bytes: int) -> None:
    """Decode a gzip archive with a strict uncompressed size limit."""

    total = 0
    with gzip.open(archive, "rb") as reader, target.open("xb") as output:
        for data in iter(lambda: reader.read(BLOCK_SIZE), b""):
            total += len(data)
            if total > max_bytes:
                raise ValueError("restored database exceeds size limit")
            output.write(data)


def check_database(archive: Path, limit: int) -> int:
    """Restore into a temporary directory and inspect SQLite integrity."""

    with tempfile.TemporaryDirectory(prefix="svt-rollback-test-") as directory:
        path = Path(directory) / "restored.sqlite3"
        restore_bounded(archive, path, limit)
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("SQLite quick_check failed")
            row = db.execute("SELECT COUNT(*) FROM measurements").fetchone()
            if row is None:
                raise ValueError("measurements table missing")
            return int(row[0])


def main() -> int:
    """Return PASS only when the rollback files can be safely inspected."""

    args = parse_args()
    if args.max_size_mib < 1:
        print("FAIL: max-size-mib must be positive", file=sys.stderr)
        return 2
    source = args.snapshot / "source.tar.gz"
    backups = list(args.snapshot.glob("voltage-*.sqlite3.gz"))
    if not source.is_file() or source.is_symlink() or len(backups) != 1:
        print("FAIL: missing or ambiguous snapshot files", file=sys.stderr)
        return 2
    if backups[0].is_symlink():
        print("FAIL: SQLite archive symlink rejected", file=sys.stderr)
        return 2
    try:
        check_source_archive(source)
        samples = check_database(backups[0], args.max_size_mib * BLOCK_SIZE)
    except (OSError, EOFError, ValueError, sqlite3.Error, tarfile.TarError):
        print("FAIL: unsafe or unreadable snapshot", file=sys.stderr)
        return 2
    print(f"SNAPSHOT CONTENT PASS: SQLite ok, {samples} samples")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
