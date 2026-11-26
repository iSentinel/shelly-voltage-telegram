#!/usr/bin/env python3
"""Rehearse durable paused-mode configuration recovery in isolated QA."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import socket
import stat
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from writer_fence import (  # noqa: E402
    WriterFenceError,
    quiescent_writer_lease,
    transaction_gate,
)

OLD = b"SCHEDULED_REPORTS_MODE=enabled\n"
NEW = b"SCHEDULED_REPORTS_MODE=paused\n"
PREFIX = "svt-report-mode-tx-qa."


def sha(data: bytes) -> str:
    """Hash exact configuration bytes."""
    return hashlib.sha256(data).hexdigest()


def protected(path: Path, directory: bool = False) -> None:
    """Reject linked or untrusted files and ancestors."""
    for item in (path, *path.parents):
        info = item.lstat()
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError("unprotected QA path")
        if item != path or directory:
            if not stat.S_ISDIR(info.st_mode):
                raise ValueError("invalid QA directory")
        elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("invalid QA file")


def require_qa(root: Path) -> None:
    """Allow only dedicated, root-owned offline rehearsal directories."""
    if (
        os.geteuid() != 0
        or socket.gethostname() != "shelly-docker-reboot-qa"
        or root.parent != Path("/var/lib")
        or not root.name.startswith(PREFIX)
        or sorted(name for _, name in socket.if_nameindex()) != ["lo"]
    ):
        raise ValueError("isolated QA only")
    protected(root, directory=True)
    protected(root / ".env")
    protected(root / "operator.lock")
    protected(root / "fence", directory=True)
    if os.environ.get("SVT_WRITER_FENCE_DIR") != str(root / "fence"):
        raise ValueError("wrong QA fence")


def sync_dir(root: Path) -> None:
    """Persist a directory entry after atomic publication."""
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write(path: Path, data: bytes) -> None:
    """Write a private file then atomically publish its durable contents."""
    fd, name = tempfile.mkstemp(prefix=".mode-tx-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        sync_dir(path.parent)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def journal_value(phase: str) -> dict:
    """Restrict the prototype to one exact enabled-to-paused transition."""
    return {
        "phase": phase,
        "old_sha256": sha(OLD),
        "new_sha256": sha(NEW),
        "runtime_verified": False,
        "deployment_authorized": False,
    }


def read_journal(root: Path) -> dict:
    """Fail closed on an unrecognized journal or arbitrary hashes."""
    path = root / "transaction.json"
    protected(path)
    if path.stat().st_size > 4096:
        raise ValueError("oversized journal")
    record = json.loads(path.read_text())
    if record not in (journal_value("prepared"), journal_value("configured")):
        raise ValueError("unrecognized journal")
    return record


def save_journal(root: Path, phase: str) -> None:
    """Publish the durable phase, never claiming runtime verification."""
    atomic_write(
        root / "transaction.json",
        (json.dumps(journal_value(phase), sort_keys=True) + "\n").encode(),
    )


def prepare(root: Path) -> dict:
    """Retain exact original bytes and publish intent before changing mode."""
    env = root / ".env"
    for name in ("transaction.json", "before.env"):
        if os.path.lexists(root / name):
            raise ValueError("existing transaction or rescue")
    if env.read_bytes() != OLD:
        raise ValueError("unexpected original QA configuration")
    atomic_write(root / "before.env", OLD)
    save_journal(root, "prepared")
    return journal_value("prepared")


def recover(root: Path) -> dict:
    """Roll forward only an exact prepared transition; never re-enable."""
    record = read_journal(root)
    protected(root / "before.env")
    if (root / "before.env").read_bytes() != OLD:
        raise ValueError("original configuration rescue changed")
    current = (root / ".env").read_bytes()
    if record["phase"] == "configured":
        if current != NEW:
            raise ValueError("configured mode drifted")
        return record
    if current not in (OLD, NEW):
        raise ValueError("unexpected configuration drift")
    if current == OLD:
        atomic_write(root / ".env", NEW)
    save_journal(root, "configured")
    return journal_value("configured")


@contextmanager
def exclusive(root: Path):
    """Require the operator lock and quiescent writer/transaction gates."""
    fd = os.open(root / "operator.lock", os.O_RDWR | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        database = root / "data/qa.sqlite3"
        with quiescent_writer_lease(database), transaction_gate(database):
            yield
    finally:
        os.close(fd)


def main() -> int:
    """Prepare or replay a configuration-only QA transaction."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "recover"))
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args()
    try:
        require_qa(args.root)
        with exclusive(args.root):
            result = (
                prepare(args.root)
                if args.action == "prepare"
                else recover(args.root)
            )
    except (OSError, ValueError, WriterFenceError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": type(exc).__name__}))
        return 3
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
