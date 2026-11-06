#!/usr/bin/env python3
"""Validate archived Shelly SQLite backups without touching live storage."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import tempfile
import time
from contextlib import closing
from pathlib import Path

from loguru import logger

BLOCK_SIZE = 1024 * 1024


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Read command options and provide the standard help message."""

    parser = argparse.ArgumentParser(
        description="Verify SHA256 and SQLite integrity in temporary files."
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=Path("/var/backups/shelly-voltage"),
        help="Directory containing voltage-*.sqlite3.gz archives.",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=3,
        help="Number of newest backups to validate (default: 3).",
    )
    parser.add_argument(
        "--max-size-mib",
        type=int,
        default=2048,
        help="Maximum unpacked size per backup (default: 2048 MiB).",
    )
    parser.add_argument(
        "--jsonl",
        action="store_true",
        help="Emit a single JSON Lines result on standard output.",
    )
    parser.add_argument(
        "--max-age-hours",
        type=float,
        help="Optionally fail backups older than this many hours.",
    )
    return parser.parse_args(argv)


def parse_sidecar(archive: Path) -> tuple[str | None, str | None]:
    """Read a checksum and verify its optional archive filename."""

    sidecar = Path(f"{archive}.sha256")
    if sidecar.is_symlink():
        return None, "SHA256 sidecar symlink rejected"
    if not sidecar.is_file():
        return None, "SHA256 sidecar missing"
    try:
        with sidecar.open("rb") as stream:
            raw = stream.read(4097)
        if len(raw) > 4096:
            return None, "invalid SHA256 sidecar"
        lines = raw.decode("utf-8").splitlines()
    except (OSError, UnicodeError):
        return None, "SHA256 sidecar unreadable"
    if len(lines) != 1:
        return None, "invalid SHA256 sidecar"
    line = lines[0]
    if not re.fullmatch(r"[0-9a-fA-F]{64}(?: [ *].+)?", line):
        return None, "invalid SHA256 sidecar"
    if len(line) > 64 and Path(line[66:]).name != archive.name:
        return None, "SHA256 filename mismatch"
    return line[:64].lower(), None


def checksum_error(archive: Path) -> str | None:
    """Compare the archived bytes with the sidecar SHA256 digest."""

    expected, problem = parse_sidecar(archive)
    if problem:
        return problem
    try:
        digest = hashlib.sha256()
        with archive.open("rb") as stream:
            for chunk in iter(lambda: stream.read(BLOCK_SIZE), b""):
                digest.update(chunk)
    except OSError:
        return "SHA256 archive unreadable"
    return None if digest.hexdigest() == expected else "SHA256 mismatch"


def unpack_archive(archive: Path, destination: Path, max_bytes: int) -> None:
    """Unpack a bounded archive into a disposable temporary directory."""

    count = 0
    with gzip.open(archive, "rb") as source, destination.open("xb") as out:
        for chunk in iter(lambda: source.read(BLOCK_SIZE), b""):
            count += len(chunk)
            if count > max_bytes:
                raise ValueError(
                    "unpacked archive exceeds the configured limit"
                )
            out.write(chunk)


def inspect_restored_database(archive: Path, max_bytes: int) -> int:
    """Open a temporary restored SQLite file and check its integrity."""

    with tempfile.TemporaryDirectory(prefix="svt-backup-check-") as directory:
        restored = Path(directory) / "restored.sqlite3"
        unpack_archive(archive, restored, max_bytes)
        with closing(sqlite3.connect(restored)) as db:
            integrity = db.execute("PRAGMA quick_check").fetchone()[0]
            if integrity != "ok":
                raise ValueError("SQLite quick_check did not pass")
            row = db.execute("SELECT COUNT(*) FROM measurements").fetchone()
            if row is None:
                raise ValueError("measurement table is missing")
            return int(row[0])


def age_error(archive: Path, max_age_hours: float | None) -> str | None:
    """Reject a stale or implausibly future-dated backup when requested."""

    if max_age_hours is None:
        return None
    try:
        age_seconds = time.time() - archive.stat().st_mtime
    except OSError:
        return "backup timestamp unreadable"
    if age_seconds < -300:
        return "backup timestamp is in the future"
    if age_seconds > max_age_hours * 3600:
        return "backup is older than allowed"
    return None


def validate_archive(
    index: int, archive: Path, limit: int, max_age_hours: float | None = None
) -> bool:
    """Check one archive, returning a stable PASS or FAIL result."""

    problem = age_error(archive, max_age_hours)
    if problem:
        logger.error("FAIL copy {}: {}", index, problem)
        return False
    problem = checksum_error(archive)
    if problem:
        logger.error("FAIL copy {}: {}", index, problem)
        return False
    try:
        samples = inspect_restored_database(archive, limit)
    except (OSError, EOFError, ValueError, sqlite3.Error) as exc:
        logger.error("FAIL copy {}: {}", index, type(exc).__name__)
        return False
    logger.info("PASS copy {}: SQLite ok, {} measurements", index, samples)
    return True


def recent_backups(directory: Path) -> list[Path] | None:
    """Find regular archives and report a directory access error."""

    try:
        with os.scandir(directory) as entries:
            paths = [
                Path(entry.path)
                for entry in entries
                if entry.name.startswith("voltage-")
                and entry.name.endswith(".sqlite3.gz")
                and entry.is_file(follow_symlinks=False)
            ]
        return sorted(
            paths, key=lambda item: item.stat().st_mtime, reverse=True
        )
    except OSError as exc:
        logger.error(
            "FAIL: cannot read backup directory ({})",
            type(exc).__name__,
        )
        return None


def report_result(
    status: str, passed: int, checked: int, requested: int, jsonl: bool
) -> int:
    """Report a stable exit status without exposing backup file paths."""

    if jsonl:
        print(
            json.dumps(
                {
                    "status": status.lower(),
                    "read_only": True,
                    "passed": passed,
                    "checked": checked,
                    "requested": requested,
                },
                sort_keys=True,
            )
        )
    logger.info("{}: {}/{} backups validated", status, passed, requested)
    return 0 if status == "PASS" else 2


def main(argv: list[str] | None = None) -> int:
    """Validate archives and return a stable PASS/FAIL exit code."""

    args = parse_args(argv)
    logger.remove()
    logger.add(sys.stderr, format="{level}: {message}")
    if args.count < 1 or args.max_size_mib < 1:
        logger.error("FAIL: count and max-size-mib must be positive")
        return report_result("FAIL", 0, 0, args.count, args.jsonl)
    if args.max_age_hours is not None and (
        not math.isfinite(args.max_age_hours) or args.max_age_hours <= 0
    ):
        logger.error("FAIL: max-age-hours must be positive and finite")
        return report_result("FAIL", 0, 0, args.count, args.jsonl)
    if not args.backup_dir.is_dir():
        logger.error("FAIL: backup directory not available")
        return report_result("FAIL", 0, 0, args.count, args.jsonl)
    archives = recent_backups(args.backup_dir)
    if archives is None:
        return report_result("FAIL", 0, 0, args.count, args.jsonl)
    selected = archives[: args.count]
    if len(selected) < args.count:
        logger.error(
            "FAIL: found {} of {} required archives",
            len(selected),
            args.count,
        )
    limit = args.max_size_mib * BLOCK_SIZE
    passed = sum(
        validate_archive(i, archive, limit, args.max_age_hours)
        for i, archive in enumerate(selected, 1)
    )
    complete = len(selected) == args.count and passed == args.count
    status = "PASS" if complete else "FAIL"
    return report_result(status, passed, len(selected), args.count, args.jsonl)


if __name__ == "__main__":
    raise SystemExit(main())
