#!/usr/bin/env python3
"""Verify B/W inode stability across synthetic source update and rollback."""

from __future__ import annotations

import argparse
import fcntl
import importlib.util
import json
import os
import stat
import tempfile
from pathlib import Path

GUARD = Path(__file__).with_name("disposable-writer-fence-container-qa.py")
CONFIRM = "OFFLINE_ROOT_OWNER_RELEASE_ROLLBACK_QA_ONLY"
ROOT_OWNER_UID = 0
WRITER_GROUP_GID = 10001


def qa_guard() -> object:
    """Use exactly the fixed-host networkless LXC114 guard."""
    spec = importlib.util.spec_from_file_location(
        "qa_owner_release_guard", GUARD
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("QA_GUARD_MISSING")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.guest_guard()
    return module


def prepare(root: Path) -> None:
    """Make only invented root-owned B/W and two source release trees."""
    root.chmod(0o700)
    owner = root / "owner"
    owner.mkdir(mode=0o750)
    os.chown(owner, ROOT_OWNER_UID, WRITER_GROUP_GID)
    os.chmod(owner, 0o750)
    for name in ("B", "W"):
        lock = owner / name
        lock.touch(mode=0o640)
        os.chown(lock, ROOT_OWNER_UID, WRITER_GROUP_GID)
        os.chmod(lock, 0o640)
    releases = root / "releases"
    releases.mkdir(mode=0o700)
    for version in ("v1", "v2"):
        folder = releases / version
        folder.mkdir(mode=0o700)
        (folder / "version.txt").write_text(version)
    os.symlink("releases/v1", root / "current")


def lock_identity(root: Path) -> dict[str, tuple[int, int, int]]:
    """Snapshot stable device/inode/mode for B and W only."""
    result = {}
    for name in ("B", "W"):
        path = root / "owner" / name
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != ROOT_OWNER_UID:
            raise RuntimeError("QA_LOCK_OWNER_OR_TYPE_CHANGED")
        if (
            stat.S_IMODE(info.st_mode) != 0o640
            or info.st_gid != WRITER_GROUP_GID
        ):
            raise RuntimeError("QA_LOCK_PERMISSION_CHANGED")
        if info.st_nlink != 1:
            raise RuntimeError("QA_LOCK_HARDLINK_DETECTED")
        result[name] = (info.st_dev, info.st_ino, info.st_mode)
    if result["B"][:2] == result["W"][:2]:
        raise RuntimeError("QA_LOCK_INODES_ALIASED")
    return result


def current(root: Path) -> str:
    """Read the active synthetic source via a root-private symlink."""
    return (root / "current" / "version.txt").read_text()


def swap(root: Path, target: str) -> None:
    """Atomically switch only the synthetic source path, never lock inodes."""
    if target not in {"v1", "v2"}:
        raise RuntimeError("QA_UNKNOWN_SYNTHETIC_RELEASE")
    stage = root / "new-current"
    os.symlink("releases/" + target, stage)
    os.replace(stage, root / "current")


def probe_lock(path: Path, exclusive: bool) -> bool:
    """Acquire a separate descriptor with an independent nonblocking flock."""
    with path.open("rb") as handle:
        try:
            mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
            fcntl.flock(handle, mode | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True


def verify_writer_blocks_maintenance(root: Path) -> None:
    """A current B SH and W EX writer must exclude updater B EX."""
    with (
        (root / "owner/B").open("rb") as b,
        (root / "owner/W").open("rb") as w,
    ):
        fcntl.flock(b, fcntl.LOCK_SH | fcntl.LOCK_NB)
        fcntl.flock(w, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if probe_lock(root / "owner/B", True):
            raise RuntimeError("QA_UPDATER_ADMITTED_DURING_WRITER")
        if probe_lock(root / "owner/W", True):
            raise RuntimeError("QA_SECOND_WRITER_ADMITTED")
        if current(root) != "v1":
            raise RuntimeError("QA_SOURCE_CHANGED_DURING_WRITER")


def change_under_maintenance(root: Path, target: str, baseline: dict) -> None:
    """Hold B EX across source update and rollback and verify stable inodes."""
    with (root / "owner/B").open("rb") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if probe_lock(root / "owner/B", False):
            raise RuntimeError("QA_WRITER_ADMITTED_DURING_MAINTENANCE")
        swap(root, target)
        if current(root) != target:
            raise RuntimeError("QA_ACTIVE_VERSION_WRONG")
        if lock_identity(root) != baseline:
            raise RuntimeError("QA_OWNER_LOCK_IDENTITY_REPLACED")


def run_suite() -> dict:
    """Validate v1->v2->v1 under one stable pair of B/W inodes."""
    with tempfile.TemporaryDirectory(
        prefix="svt-qa-owner-release-", dir="/var/tmp"
    ) as name:
        root = Path(name)
        prepare(root)
        baseline = lock_identity(root)
        verify_writer_blocks_maintenance(root)
        change_under_maintenance(root, "v2", baseline)
        change_under_maintenance(root, "v1", baseline)
        if not probe_lock(root / "owner/B", True):
            raise RuntimeError("QA_LOCK_NOT_RELEASED_AT_END")
        return {
            "result": "PASS_STABLE_B_W_INODES_UPDATE_ROLLBACK",
            "synthetic_releases": ["v1", "v2", "v1"],
            "B_W_owner_inodes_stable": True,
            "writer_excludes_maintenance": True,
            "maintenance_excludes_writer": True,
            "docker_used": False,
            "production_changed": False,
        }


def main() -> int:
    """Be inert by default and require exact LXC114 consent."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-disposable", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    if not args.run_disposable:
        print("DRY_RUN: no source, lock, or Docker paths changed")
        return 0
    if args.confirm != CONFIRM:
        print("BLOCKED: exact offline QA confirmation required")
        return 2
    try:
        qa_guard()
        evidence = run_suite()
    except (OSError, RuntimeError) as error:
        print("QA_BLOCKED", type(error).__name__, str(error)[:250])
        return 3
    print(json.dumps(evidence, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
