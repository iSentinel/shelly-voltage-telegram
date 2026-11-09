#!/usr/bin/env python3
"""Verify that a source snapshot is a complete safe rollback archive."""

from __future__ import annotations

import argparse
import gzip
import sys
import tarfile
from pathlib import Path, PurePosixPath

MAX_FILES = 20000
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_UNPACKED_BYTES = 600 * 1024 * 1024
REQUIRED = {
    "VERSION",
    "app.py",
    "Dockerfile",
    "compose.yaml",
    "requirements.txt",
    "scripts/backup.sh",
    "systemd/shelly-voltage-compose.service",
    "systemd/shelly-voltage-backup.service",
}


def check_members(path: Path) -> None:
    """Reject unsafe members and archives without rollback essentials."""

    count = 0
    total = 0
    present: set[str] = set()
    seen: set[str] = set()
    with tarfile.open(path, "r:gz") as archive:
        for entry in archive:
            count += 1
            total += entry.size
            member_path = PurePosixPath(entry.name)
            parts = member_path.parts
            if (
                entry.name.startswith("/")
                or ".." in parts
                or not (entry.isfile() or entry.isdir())
                or entry.mode & 0o6000
                or count > MAX_FILES
                or total > MAX_FILE_BYTES
            ):
                raise ValueError("unsafe source archive member")
            name = str(member_path)
            if name in seen:
                raise ValueError("duplicate source archive member")
            seen.add(name)
            if entry.isfile() and entry.size > 0:
                present.add(name)
    if not REQUIRED.issubset(present):
        raise ValueError("source archive missing required application files")


def check_compressed_bytes(path: Path) -> None:
    """Read bounded gzip content fully, including its trailing checksum."""

    total = 0
    with gzip.open(path, "rb") as stream:
        while block := stream.read(1024 * 1024):
            total += len(block)
            if total > MAX_UNPACKED_BYTES:
                raise ValueError("source archive expanded beyond size limit")


def main() -> int:
    """Provide a stable PASS or FAIL status for both installer and rollback."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    try:
        check_members(args.archive)
        check_compressed_bytes(args.archive)
    except (OSError, EOFError, ValueError, tarfile.TarError) as exc:
        print(f"ERROR: invalid source archive: {exc}", file=sys.stderr)
        return 3
    print("SOURCE ARCHIVE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
