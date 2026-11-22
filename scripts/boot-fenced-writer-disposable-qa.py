#!/usr/bin/env python3
"""Private synthetic Pro3EM writer and updater lock experiment only."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path

IDENTITY = "DISPOSABLE_PRO3EM_SQLITE_TEST_ONLY"


class Blocked(Exception):
    """The requested operation is unsafe for a synthetic QA fixture."""


def fixture_paths(root: Path) -> tuple[Path, Path, Path]:
    """Require explicit disposable identity and no linked data paths."""

    if root.is_symlink() or not root.is_dir():
        raise Blocked("QA_ROOT_UNSAFE")
    if (root / "qa-identity").read_text().strip() != IDENTITY:
        raise Blocked("QA_IDENTITY_INVALID")
    data = root / "history.sqlite3"
    lock = root / "writer.lock"
    marker = root / "pending-update"
    for path in (data, lock, marker, root / "owner.lock"):
        if path.is_symlink():
            raise Blocked("QA_LINKED_PATH")
        if path.exists() and path.stat().st_nlink != 1:
            raise Blocked("QA_LINKED_PATH")
    if not data.is_file():
        raise Blocked("QA_DATABASE_MISSING")
    return data, lock, marker


def enter_lock(path: Path) -> int:
    """Acquire the updater/writer common local OS lock."""

    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
    except BaseException:
        os.close(fd)
        raise
    return fd


def enter_owner_lease(path: Path) -> int:
    """Hold a lifetime lease so two synthetic writers cannot coexist."""

    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(fd)
        raise Blocked("WRITER_ALREADY_ACTIVE") from exc
    except BaseException:
        os.close(fd)
        raise
    return fd


def write_triplet(db_path: Path, marker: Path, index: int) -> None:
    """Commit all three phases in one synthetic SQLite transaction."""

    if marker.exists() or marker.is_symlink():
        raise Blocked("PENDING_FENCED")
    with closing(sqlite3.connect(db_path, timeout=5)) as db:
        db.execute("BEGIN IMMEDIATE")
        if marker.exists() or marker.is_symlink():
            db.rollback()
            raise Blocked("PENDING_FENCED")
        stamp = 1800000000.0 + index
        db.executemany(
            "INSERT INTO measurements(ts,phase,voltage,frequency) "
            "VALUES(?,?,?,?)",
            [
                (stamp, name, 208.0 + offset, 50.0)
                for offset, name in enumerate(("L1", "L2", "L3"))
            ],
        )
        db.commit()


def publish_pending(marker: Path) -> None:
    """Publish a synthetic pending marker while holding the shared lock."""

    if marker.exists() or marker.is_symlink():
        raise Blocked("PENDING_ALREADY_EXISTS")
    fd = os.open(
        marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(fd, "w") as stream:
        stream.write("synthetic-test-marker\n")
        stream.flush()
        os.fsync(stream.fileno())


def execute(root: Path, mode: str, count: int, interval: float) -> dict:
    """Run bounded synthetic writes or one atomic updater marker."""

    db, token, marker = fixture_paths(root)
    owner_fd = (
        enter_owner_lease(root / "owner.lock") if mode == "writer" else None
    )
    try:
        for index in range(count if mode == "writer" else 1):
            fd = enter_lock(token)
            try:
                if mode == "publish":
                    publish_pending(marker)
                else:
                    write_triplet(db, marker, index)
            finally:
                os.close(fd)
            if mode == "writer":
                time.sleep(interval)
    finally:
        if owner_fd is not None:
            os.close(owner_fd)
    return {"status": "PASS", "mode": mode, "cycles": count}


def main() -> int:
    """Fail closed outside the private disposable fixture."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--mode", choices=("writer", "publish"), required=True)
    parser.add_argument("--cycles", type=int, default=10)
    parser.add_argument("--interval", type=float, default=0.15)
    args = parser.parse_args()
    if not 1 <= args.cycles <= 2000 or not 0 <= args.interval <= 2:
        parser.error("bounded cycles and interval required")
    try:
        result = execute(args.root, args.mode, args.cycles, args.interval)
    except (Blocked, OSError, sqlite3.Error) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}))
        return 2
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
