#!/usr/bin/env python3
"""Atomically publish or clear the SQLite writer's shared update marker."""

from __future__ import annotations

import argparse
import os
import re
import stat
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from writer_fence import WriterFenceError, update_gate  # noqa: E402

TOKEN_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}\Z")


def parse_args() -> argparse.Namespace:
    """Require an exact database path and explicit update action."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("publish", "clear", "status"))
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--token", default="")
    return parser.parse_args()


def marker_value(marker: Path) -> str | None:
    """Read only a small, regular, singly-linked non-symlink marker."""
    try:
        fd = os.open(marker, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_size > 128
        ):
            raise WriterFenceError("SVT_PENDING_MARKER_UNSAFE")
        return os.read(fd, 129).decode("ascii").strip()
    finally:
        os.close(fd)


def sync_directory(directory: Path) -> None:
    """Persist a created or deleted marker directory entry."""
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def publish(marker: Path, token: str) -> None:
    """Atomically add an idempotent marker without replacing another."""
    current = marker_value(marker)
    if current is not None:
        if current != token:
            raise WriterFenceError("SVT_PENDING_CONFLICT")
        return
    fd, temp = tempfile.mkstemp(prefix=".svt-pending-", dir=marker.parent)
    try:
        os.fchmod(fd, 0o644)
        os.write(fd, (token + "\n").encode("ascii"))
        os.fsync(fd)
        os.link(temp, marker, follow_symlinks=False)
        os.unlink(temp)
        sync_directory(marker.parent)
    finally:
        os.close(fd)
        if os.path.lexists(temp):
            os.unlink(temp)


def clear(marker: Path, token: str) -> None:
    """Remove only the exact expected marker, never another update."""
    current = marker_value(marker)
    if current is None:
        raise WriterFenceError("SVT_PENDING_NOT_FOUND")
    if current != token:
        raise WriterFenceError("SVT_PENDING_CONFLICT")
    os.unlink(marker)
    sync_directory(marker.parent)


def main() -> int:
    """Run marker operations under the same gate as Storage.connect."""
    args = parse_args()
    if not args.database.is_absolute():
        print("FAIL: DATABASE_PATH_NOT_ABSOLUTE", file=sys.stderr)
        return 2
    if args.action != "status" and not TOKEN_PATTERN.fullmatch(args.token):
        print("FAIL: INVALID_TOKEN", file=sys.stderr)
        return 2
    try:
        marker = args.database.parent / ".svt-update-pending"
        with update_gate(args.database):
            if args.action == "publish":
                publish(marker, args.token)
            elif args.action == "clear":
                clear(marker, args.token)
            else:
                pending = marker_value(marker) is not None
                print("PENDING" if pending else "CLEAR")
                return 3 if pending else 0
    except (OSError, UnicodeError, WriterFenceError) as exc:
        reason = (
            str(exc)
            if isinstance(exc, WriterFenceError)
            else type(exc).__name__
        )
        print(f"FAIL: {reason}", file=sys.stderr)
        return 3
    print(f"PASS: {args.action.upper()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
