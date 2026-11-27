#!/usr/bin/env python3
"""Rehearse legacy public receipt migration only in isolated QA."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import socket
import stat
import subprocess
from pathlib import Path

NAME = "shelly-voltage-update-check"
TARGET = "private/" + NAME
JOURNAL = "migration.json"
STAGED = "public-staged"


def directory(path: Path, mode: int, uid: int = 0) -> os.stat_result:
    """Require an ordinary directory with reviewed owner and permissions."""
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_IMODE(info.st_mode) != mode
        or info.st_uid != uid
    ):
        raise ValueError("unexpected directory identity")
    return info


def identity(info: os.stat_result) -> list[int]:
    """Return device, inode and owner for a durable comparison."""
    return [info.st_dev, info.st_ino, info.st_uid]


def sync(root: Path) -> None:
    """Flush the parent directory after namespace operations."""
    fd = os.open(root, os.O_DIRECTORY | os.O_RDONLY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def private_snapshot(root: Path) -> dict:
    """Bind the private target and receipt without copying its contents."""
    parent = root / "private"
    directory(parent, 0o700)
    target = parent / NAME
    info = target.lstat()
    if info.st_uid == 0:
        raise ValueError("expected nonroot private state owner")
    directory(target, 0o750, info.st_uid)
    receipt = target / "deployed.json"
    record = receipt.lstat()
    if (
        not stat.S_ISREG(record.st_mode)
        or record.st_nlink != 1
        or record.st_uid != info.st_uid
        or record.st_mode & 0o022
        or not 0 < record.st_size <= 8192
    ):
        raise ValueError("unexpected private receipt")
    return {
        "target": identity(info),
        "receipt": identity(record),
        "receipt_sha256": hashlib.sha256(receipt.read_bytes()).hexdigest(),
    }


def alias_identity(root: Path) -> list[int]:
    """Require the exact root-owned literal legacy symlink."""
    alias = root / NAME
    info = alias.lstat()
    if (
        not stat.S_ISLNK(info.st_mode)
        or info.st_uid != 0
        or info.st_nlink != 1
        or os.readlink(alias) != TARGET
    ):
        raise ValueError("unexpected legacy alias")
    return identity(info)


def read_journal(root: Path) -> dict:
    """Read only a private root-owned migration checkpoint."""
    path = root / JOURNAL
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o600
        or not 0 < info.st_size <= 8192
    ):
        raise ValueError("unsafe migration journal")
    result = json.loads(path.read_bytes())
    if (
        set(result) != {"schema", "alias", "private", "staged", "qa_only"}
        or type(result["schema"]) is not int
        or result["schema"] != 1
        or result["qa_only"] is not True
    ):
        raise ValueError("invalid migration journal")
    return result


def prepare(root: Path) -> dict:
    """Durably record the exact alias and replacement before any unlink."""
    if os.path.lexists(root / JOURNAL) or os.path.lexists(root / STAGED):
        raise ValueError("existing migration state")
    alias = alias_identity(root)
    private = private_snapshot(root)
    staged = root / STAGED
    staged.mkdir(mode=0o755)
    staged.chmod(0o755)
    sync(root)
    record = {
        "schema": 1,
        "alias": alias,
        "private": private,
        "staged": identity(directory(staged, 0o755)),
        "qa_only": True,
    }
    path = root / JOURNAL
    with path.open("xb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(json.dumps(record, sort_keys=True).encode() + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    sync(root)
    return record


def empty_public(path: Path, expected: list[int]) -> None:
    """Accept only the original empty prepared directory."""
    if identity(directory(path, 0o755)) != expected or any(path.iterdir()):
        raise ValueError("unexpected replacement directory")


def recover(root: Path) -> dict:
    """Complete only the recorded migration; never copy a private receipt."""
    record = read_journal(root)
    if private_snapshot(root) != record["private"]:
        raise ValueError("private receipt changed")
    alias, staged = root / NAME, root / STAGED
    if not os.path.lexists(staged):
        empty_public(alias, record["staged"])
    else:
        empty_public(staged, record["staged"])
        if os.path.lexists(alias):
            if alias_identity(root) != record["alias"]:
                raise ValueError("legacy alias identity changed")
            alias.unlink()
            sync(root)
        staged.rename(alias)
        sync(root)
        empty_public(alias, record["staged"])
    if private_snapshot(root) != record["private"]:
        raise ValueError("private receipt changed during migration")
    return {
        "status": "PASS_QA_RECEIPT_MIGRATION_ONLY",
        "private_preserved": True,
        "public_empty": True,
        "deployment_authorized": False,
    }


def require_qa(root: Path) -> None:
    """Confine all namespace operations to a private offline QA fixture."""
    if (
        os.geteuid() != 0
        or root.parent != Path("/var/lib")
        or not re.fullmatch(
            r"svt-legacy-receipt-qa\.[A-Za-z0-9]{6}", root.name
        )
        or socket.gethostname() != "shelly-docker-reboot-qa"
        or sorted(name for _, name in socket.if_nameindex()) != ["lo"]
    ):
        raise ValueError("isolated QA fixture required")
    directory(root, 0o700)
    for parent in root.parents:
        info = parent.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != 0
            or info.st_mode & 0o022
        ):
            raise ValueError("unsafe fixture parent")


def require_inactive(root: Path) -> None:
    """Verify dedicated real QA systemd timer and service are retired."""
    for kind in ("timer", "service"):
        unit = root.name + "." + kind
        result = subprocess.run(
            [
                "systemctl",
                "show",
                unit,
                "-p",
                "LoadState",
                "-p",
                "ActiveState",
                "-p",
                "UnitFileState",
                "-p",
                "NeedDaemonReload",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        facts = dict(line.split("=", 1) for line in result.stdout.splitlines())
        if facts != {
            "LoadState": "loaded",
            "ActiveState": "inactive",
            "UnitFileState": "disabled" if kind == "timer" else "static",
            "NeedDaemonReload": "no",
        }:
            raise ValueError("legacy QA checker not retired")


def main() -> int:
    """Prepare or recover an isolated rehearsal, never production."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("action", choices=("prepare", "recover"))
    args = parser.parse_args()
    try:
        require_qa(args.root)
        lock = args.root / "operator.lock"
        fd = os.open(lock, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                info.st_uid != 0
                or not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600
            ):
                raise ValueError("unsafe operator lock")
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            require_inactive(args.root)
            result = (
                prepare(args.root)
                if args.action == "prepare"
                else recover(args.root)
            )
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        subprocess.SubprocessError,
    ) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": type(exc).__name__}))
        return 3
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
