#!/usr/bin/env python3
"""Check an archived SQLite database without affecting production."""

from __future__ import annotations

import argparse
import gzip
import importlib.util
import sqlite3
import stat
import sys
import tempfile
from contextlib import closing
from pathlib import Path
from urllib.parse import quote

BLOCK_SIZE = 1024 * 1024


class RetainedBootEligible(ValueError):
    """The healthy databases differ only in data and can share the schema."""


def parse_args() -> argparse.Namespace:
    """Read the archive path and maximum restored database size."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--max-size-mib", type=int, default=2048)
    parser.add_argument("--compare-db", type=Path)
    parser.add_argument(
        "--retained-boot",
        action="store_true",
        help="Return 5 for changed data with a compatible live schema",
    )
    args = parser.parse_args()
    if args.retained_boot and args.compare_db is None:
        parser.error("--retained-boot requires --compare-db")
    return args


def restore_bounded(archive: Path, destination: Path, max_bytes: int) -> None:
    """Limit expanded bytes to avoid filling the temporary filesystem."""

    total = 0
    with gzip.open(archive, "rb") as source, destination.open("xb") as output:
        for block in iter(lambda: source.read(BLOCK_SIZE), b""):
            total += len(block)
            if total > max_bytes:
                raise ValueError("restored database exceeds size limit")
            output.write(block)


def full_sqlite_digest():
    """Load the project's strict logical SQLite digest implementation."""

    source = Path(__file__).with_name("em50-predeploy-backup-guard.py")
    spec = importlib.util.spec_from_file_location("em50_rescue_parity", source)
    if spec is None or spec.loader is None:
        raise ValueError("SQLite parity implementation unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    digest = getattr(module, "whole_sqlite_digest", None)
    if not callable(digest):
        raise ValueError("SQLite digest implementation missing")
    return digest


def database_schema(db: sqlite3.Connection) -> tuple:
    """Compare exact definitions and the application's migration version."""

    definitions = db.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_schema "
        "ORDER BY type, name"
    ).fetchall()
    version = db.execute(
        "SELECT value FROM meta WHERE key='schema_version'"
    ).fetchone()
    if (
        version is None
        or not str(version[0]).isascii()
        or not str(version[0]).isdigit()
    ):
        raise ValueError("application schema version is invalid")
    user = db.execute("PRAGMA user_version").fetchone()
    app = db.execute("PRAGMA application_id").fetchone()
    return definitions, version, user, app


def retained_boot_compatible(
    live: sqlite3.Connection, saved: sqlite3.Connection
) -> bool:
    """Protect the known schema and unresolved Telegram delivery state."""

    pending = live.execute(
        "SELECT 1 FROM meta WHERE key IN "
        "('telegram_graphic_publish_pending', "
        "'telegram_legacy_text_confirm_pending', "
        "'telegram_recovery_rotation_pending_at') LIMIT 1"
    ).fetchone()
    return pending is None and database_schema(live) == database_schema(saved)


def compare_full_sqlite(
    restored: Path, current: Path, retained_boot: bool = False
) -> None:
    """Require full logical parity with stopped live SQLite, read-only."""

    if any(part.is_symlink() for part in (current, *current.parents)):
        raise ValueError("SQLite compare source path is linked")
    info = current.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("SQLite compare source must be independent")
    uri = "file:" + quote(str(current.absolute())) + "?mode=ro"
    with (
        closing(sqlite3.connect(uri, uri=True)) as live,
        closing(sqlite3.connect(restored)) as saved,
    ):
        live.execute("BEGIN")
        saved.execute("BEGIN")
        if live.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("live SQLite quick_check failed")
        digest = full_sqlite_digest()
        if digest(live) != digest(saved):
            if retained_boot and retained_boot_compatible(live, saved):
                raise RetainedBootEligible("live history differs")
            raise ValueError("rescue SQLite whole-history parity failed")


def validate_database(
    archive: Path,
    max_bytes: int,
    compare_db: Path | None = None,
    retained_boot: bool = False,
) -> int:
    """Restore into a temporary file and verify basic database integrity."""

    with tempfile.TemporaryDirectory(prefix="svt-check-") as directory:
        restored = Path(directory) / "restored.sqlite3"
        restore_bounded(archive, restored, max_bytes)
        connection = sqlite3.connect(f"file:{restored}?mode=ro", uri=True)
        with closing(connection) as db:
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("SQLite quick_check failed")
            row = db.execute("SELECT COUNT(*) FROM measurements").fetchone()
            if row is None:
                raise ValueError("measurements table missing")
            if compare_db is not None:
                compare_full_sqlite(restored, compare_db, retained_boot)
            return int(row[0])


def main() -> int:
    """Return 2 on invalid evidence and 5 for boot-compatible data drift."""

    args = parse_args()
    if args.max_size_mib < 1 or args.archive.is_symlink():
        print("FAIL: invalid size limit or archive symlink", file=sys.stderr)
        return 2
    if not args.archive.is_file():
        print("FAIL: backup archive missing", file=sys.stderr)
        return 2
    try:
        count = validate_database(
            args.archive,
            args.max_size_mib * BLOCK_SIZE,
            args.compare_db,
            args.retained_boot,
        )
    except RetainedBootEligible:
        print(
            "SQLite data differs with matching schema; rollback blocked",
            file=sys.stderr,
        )
        return 5
    except (OSError, EOFError, ValueError, sqlite3.Error):
        print("FAIL: invalid SQLite archive", file=sys.stderr)
        return 2
    print(f"SQLITE ARCHIVE PASS: {count} restored measurements")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
