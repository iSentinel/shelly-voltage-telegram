#!/usr/bin/env python3
"""Create the root-owned shared lock directory for the nonroot monitor."""

from __future__ import annotations

import argparse
import os
import stat
import sys
from pathlib import Path


def arguments() -> argparse.Namespace:
    """Accept a specific root-owned directory and expected process UID."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--uid", type=int, default=10001)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def validate_parent(path: Path) -> None:
    """Reject symlinked, non-root or writable ancestor directories."""
    if not path.is_absolute() or path == Path("/"):
        raise ValueError("SVT_FENCE_DIR_INVALID")
    for part in (*reversed(path.parent.parents), path.parent):
        info = part.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != 0
            or info.st_mode & 0o022
        ):
            raise ValueError("SVT_FENCE_PARENT_UNSAFE")


def validate_directory(path: Path) -> None:
    """Require the marker directory to be owned only by root."""
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != 0
        or stat.S_IMODE(info.st_mode) != 0o755
    ):
        raise ValueError("SVT_FENCE_DIR_UNSAFE")


def initialize_lock(path: Path, uid: int) -> None:
    """Idempotently create a nonroot-owned regular lock file."""
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
        created = False
    else:
        created = True
    try:
        if created:
            os.fchown(fd, uid, uid)
            os.fchmod(fd, 0o600)
            os.fsync(fd)
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != uid
            or info.st_gid != uid
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise ValueError("SVT_LOCK_PATH_UNSAFE")
    finally:
        os.close(fd)


def main() -> int:
    """Prepare an isolated shared mount, never touch existing markers."""
    args = arguments()
    try:
        if os.geteuid() != 0 or args.uid <= 0:
            raise ValueError("SVT_FENCE_ROOT_REQUIRED")
        validate_parent(args.directory)
        if args.dry_run:
            print("DRY-RUN: validate and provision shared writer fence")
            return 0
        args.directory.mkdir(mode=0o755, exist_ok=True)
        validate_directory(args.directory)
        for name in (".svt-owner.lock", ".svt-transaction.lock"):
            initialize_lock(args.directory / name, args.uid)
    except (OSError, ValueError) as exc:
        code = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        print(f"FAIL: {code}", file=sys.stderr)
        return 3
    print("PASS: SHARED_WRITER_FENCE_READY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
