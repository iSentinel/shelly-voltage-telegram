#!/usr/bin/env python3
"""Check an archived SQLite database without affecting production."""

from __future__ import annotations

import argparse
import gzip
import sqlite3
import sys
import tempfile
from pathlib import Path

BLOCK_SIZE = 1024 * 1024


def parse_args() -> argparse.Namespace:
    """Read the archive path and maximum restored database size."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--max-size-mib", type=int, default=2048)
    return parser.parse_args()


def restore_bounded(archive: Path, destination: Path, max_bytes: int) -> None:
    """Limit expanded bytes to avoid filling the temporary filesystem."""

    total = 0
    with gzip.open(archive, "rb") as source, destination.open("xb") as output:
        for block in iter(lambda: source.read(BLOCK_SIZE), b""):
            total += len(block)
            if total > max_bytes:
                raise ValueError("restored database exceeds size limit")
            output.write(block)


def validate_database(archive: Path, max_bytes: int) -> int:
    """Restore into a temporary file and verify basic database integrity."""

    with tempfile.TemporaryDirectory(prefix="svt-rollback-check-") as directory:
        restored = Path(directory) / "restored.sqlite3"
        restore_bounded(archive, restored, max_bytes)
        with sqlite3.connect(f"file:{restored}?mode=ro", uri=True) as db:
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("SQLite quick_check failed")
            row = db.execute("SELECT COUNT(*) FROM measurements").fetchone()
            if row is None:
                raise ValueError("measurements table missing")
            return int(row[0])


def main() -> int:
    """Return code 2 if any check cannot be completed safely."""

    args = parse_args()
    if args.max_size_mib < 1 or args.archive.is_symlink():
        print("FAIL: invalid size limit or archive symlink", file=sys.stderr)
        return 2
    if not args.archive.is_file():
        print("FAIL: backup archive missing", file=sys.stderr)
        return 2
    try:
        count = validate_database(args.archive, args.max_size_mib * BLOCK_SIZE)
    except (OSError, EOFError, ValueError, sqlite3.Error):
        print("FAIL: archived SQLite is corrupt or unreadable", file=sys.stderr)
        return 2
    print(f"SQLITE ARCHIVE PASS: {count} restored measurements")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
