#!/usr/bin/env python3
"""Test no-gap admission G EX before incumbent stop and B EX then A EX."""

from __future__ import annotations

import argparse
import fcntl
import importlib.util
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

GUARD = Path(__file__).with_name("disposable-writer-fence-container-qa.py")
CONFIRM = "OFFLINE_ADMISSION_G_B_A_HANDOFF_QA_ONLY"

CHILD = """
import fcntl
import sys
import time
from pathlib import Path

root = Path(sys.argv[1])
name = sys.argv[2]
status = root / (name + ".status")
stop = root / (name + ".stop")
try:
    with (root / "G").open("rb") as g:
        fcntl.flock(g, fcntl.LOCK_SH | fcntl.LOCK_NB)
        with (root / "B").open("rb") as b:
            fcntl.flock(b, fcntl.LOCK_SH | fcntl.LOCK_NB)
            with (root / "W").open("rb") as w:
                fcntl.flock(w, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(g, fcntl.LOCK_UN)
                status.write_text("RUNNING")
                deadline = time.monotonic() + 12
                while not stop.exists():
                    if time.monotonic() > deadline:
                        raise SystemExit(5)
                    time.sleep(0.05)
                status.write_text("STOPPED")
except BlockingIOError:
    status.write_text("DENIED")
    raise SystemExit(20)
"""


def offline_guard() -> None:
    """Refuse all activity without the existing exact isolated LXC114 guard."""
    spec = importlib.util.spec_from_file_location("qa_g_handoff_guard", GUARD)
    if spec is None or spec.loader is None:
        raise RuntimeError("EXACT_QA_GUARD_NOT_FOUND")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.guest_guard()


def prepare(root: Path) -> dict[str, tuple[int, int]]:
    """Create private stable G/B/W/A inodes with no Docker or SQLite."""
    root.chmod(0o700)
    metadata = {}
    for name in ("G", "B", "W", "A"):
        lock = root / name
        lock.touch(mode=0o600, exist_ok=False)
        lock.chmod(0o600)
        info = lock.stat()
        metadata[name] = (info.st_dev, info.st_ino)
    return metadata


def await_marker(root: Path, name: str, expected: str) -> None:
    """Require a bounded exact process handshake before proceeding."""
    marker = root / (name + ".status")
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        if marker.exists():
            if marker.read_text() != expected:
                raise RuntimeError("UNEXPECTED_SYNTHETIC_CHILD_STATE")
            return
        time.sleep(0.03)
    raise RuntimeError("SYNTHETIC_CHILD_HANDSHAKE_TIMEOUT")


def worker(root: Path, name: str) -> subprocess.Popen:
    """Start only an invented Python writer without external services."""
    return subprocess.Popen(
        [sys.executable, "-c", CHILD, str(root), name],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def can_lock(root: Path, name: str, mode: int) -> bool:
    """Attempt a separate nonblocking lock without keeping ownership."""
    with (root / name).open("rb") as holder:
        try:
            fcntl.flock(holder, mode | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True


def stop_worker(root: Path, name: str, child: subprocess.Popen) -> None:
    """Request only this synthetic worker's graceful exit."""
    (root / (name + ".stop")).touch()
    if child.wait(timeout=5) != 0:
        raise RuntimeError("SYNTHETIC_WRITER_FAILED_TO_STOP")


def reap(children: list[subprocess.Popen]) -> None:
    """Only terminate and reap subprocesses this test started."""
    for child in children:
        if child.poll() is None:
            child.terminate()
        try:
            child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=3)


def maintenance(
    root: Path, incumbent: subprocess.Popen, children: list[subprocess.Popen]
) -> None:
    """Get admission EX before stopping incumbent and taking B EX/A EX."""
    with (root / "G").open("rb") as g:
        fcntl.flock(g, fcntl.LOCK_EX | fcntl.LOCK_NB)
        contender = worker(root, "contender")
        children.append(contender)
        await_marker(root, "contender", "DENIED")
        if contender.wait(timeout=4) != 20:
            raise RuntimeError("ADMISSION_G_DID_NOT_BLOCK_NEW_WRITER")
        stop_worker(root, "incumbent", incumbent)
        with (
            (root / "B").open("rb") as b,
            (root / "A").open("rb") as a,
        ):
            fcntl.flock(b, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(a, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if can_lock(root, "B", fcntl.LOCK_SH):
                raise RuntimeError("NEW_WRITER_ADMITTED_UNDER_B_EX")


def verify_inodes(root: Path, baseline: dict) -> None:
    """Enforce that no lock pathname inode was replaced during handoff."""
    for name, expected in baseline.items():
        info = (root / name).stat()
        if (info.st_dev, info.st_ino) != expected:
            raise RuntimeError("QA_OWNER_LOCK_INODE_REPLACED")


def run_handoff(root: Path, baseline: dict) -> dict:
    """Prove no gap between admission freeze and B/A maintenance."""
    children = []
    incumbent = worker(root, "incumbent")
    children.append(incumbent)
    try:
        await_marker(root, "incumbent", "RUNNING")
        if can_lock(root, "B", fcntl.LOCK_EX):
            raise RuntimeError("INCUMBENT_B_SH_MISSING")
        maintenance(root, incumbent, children)
        resumed = worker(root, "resumed")
        children.append(resumed)
        await_marker(root, "resumed", "RUNNING")
        stop_worker(root, "resumed", resumed)
        verify_inodes(root, baseline)
        return {
            "result": "PASS_G_EX_STOP_B_EX_A_EX_HANDOFF",
            "G_EX_while_incumbent_B_SH_active": True,
            "second_writer_blocked_at_admission": True,
            "B_EX_and_A_EX_after_stop": True,
            "resumed_after_release": True,
            "all_G_B_W_A_inodes_stable": True,
            "docker_used": False,
            "sqlite_used": False,
            "production_modified": False,
        }
    finally:
        reap(children)


def run_suite() -> dict:
    """Keep all locks, process markers and roles in disposable scratch."""
    with tempfile.TemporaryDirectory(
        prefix="svt-qa-gate-handoff-", dir="/var/tmp"
    ) as path:
        root = Path(path)
        return run_handoff(root, prepare(root))


def main() -> int:
    """Default inert and exact offline consent required."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-disposable", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    if not args.run_disposable:
        print("DRY_RUN: no subprocesses, locks, or Docker activity")
        return 0
    if args.confirm != CONFIRM:
        print("BLOCKED: exact isolated QA confirmation required")
        return 2
    try:
        offline_guard()
        evidence = run_suite()
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print("QA_BLOCKED", type(error).__name__, str(error)[:240])
        return 3
    print(json.dumps(evidence, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
