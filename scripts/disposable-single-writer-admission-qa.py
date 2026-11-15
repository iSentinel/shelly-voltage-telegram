#!/usr/bin/env python3
"""Compare B SH and separate exclusive writer admission on LXC114."""

from __future__ import annotations

import argparse
import fcntl
import importlib.util
import json
import subprocess
import tempfile
import time
from pathlib import Path

GUARD_SOURCE = (
    Path(__file__).resolve().with_name("disposable-inherited-b-lifetime-qa.py")
)
CONFIRM = "OFFLINE_SINGLE_WRITER_ADMISSION_QA_ONLY"


def load_guard() -> object:
    """Use the fixed local loopback-only LXC114 guard from sibling QA."""
    spec = importlib.util.spec_from_file_location(
        "qa_inherited_B_guard", GUARD_SOURCE
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("QA_GUARD_NOT_FOUND")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def lock_try_exclusive(path: Path) -> bool:
    """Try a separate exclusive lock without retaining ownership."""
    with path.open("rb") as file:
        try:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True


def prepare(root: Path) -> None:
    """Create two separate root-owned QA lock inodes in private scratch."""
    root.mkdir(mode=0o700)
    (root / "maintenance.lock").touch(mode=0o600, exist_ok=False)
    (root / "writer.lock").touch(mode=0o600, exist_ok=False)


def command(root: Path, single_writer: bool) -> list[str]:
    """Build real flock exec chain using no-fork to retain both locks."""
    result = [
        "/usr/bin/flock",
        "--no-fork",
        "--shared",
        "--nonblock",
        str(root / "maintenance.lock"),
    ]
    if single_writer:
        result.extend(
            [
                "/usr/bin/flock",
                "--no-fork",
                "--exclusive",
                "--nonblock",
                str(root / "writer.lock"),
            ]
        )
    result.extend(["/usr/bin/sleep", "3"])
    return result


def spawn(root: Path, single_writer: bool) -> subprocess.Popen:
    """Start only a bounded synthetic sleep via real flock exec."""
    return subprocess.Popen(
        command(root, single_writer),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def wait_lock_claim(
    root: Path, worker: subprocess.Popen, single_writer: bool
) -> None:
    """Verify synthetic worker claims both expected inodes."""
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if worker.poll() is not None:
            raise RuntimeError("QA_WORKER_EXITED_BEFORE_LOCK_CLAIM")
        B = not lock_try_exclusive(root / "maintenance.lock")
        W = not lock_try_exclusive(root / "writer.lock")
        if B and (W if single_writer else not W):
            return
        time.sleep(0.03)
    raise RuntimeError("QA_LOCK_CLAIM_TIMEOUT")


def finish(worker: subprocess.Popen) -> None:
    """Reap and terminate only the owned no-fork synthetic process."""
    if worker.poll() is None:
        worker.terminate()
    try:
        worker.wait(timeout=3)
    except subprocess.TimeoutExpired:
        worker.kill()
        worker.wait(timeout=3)


def shared_only_case(root: Path) -> dict:
    """Show two writers concurrently acquire B SH without W exclusion."""
    prepare(root)
    first = spawn(root, False)
    second = None
    try:
        wait_lock_claim(root, first, False)
        second = spawn(root, False)
        wait_lock_claim(root, second, False)
        if first.poll() is not None or second.poll() is not None:
            raise RuntimeError("CONCURRENT_B_SH_MISSING")
        if lock_try_exclusive(root / "maintenance.lock"):
            raise RuntimeError("B_EX_INCORRECTLY_AVAILABLE")
        return {
            "case": "B_SH_ONLY",
            "simultaneous_writers": 2,
            "B_EX_during_writers": "BLOCKED",
            "classification": "SINGLE_WRITER_VIOLATION",
        }
    finally:
        if second is not None:
            finish(second)
        finish(first)


def exclusive_writer_case(root: Path) -> dict:
    """Show first B SH + W EX writer blocks second W EX admission."""
    prepare(root)
    first = spawn(root, True)
    second = None
    try:
        wait_lock_claim(root, first, True)
        second = spawn(root, True)
        exit_code = second.wait(timeout=3)
        if exit_code == 0 or first.poll() is not None:
            raise RuntimeError("EXCLUSIVE_WRITER_LOCK_FAILED")
        if lock_try_exclusive(root / "maintenance.lock") or lock_try_exclusive(
            root / "writer.lock"
        ):
            raise RuntimeError("UNSAFE_LOCK_RELEASE")
        return {
            "case": "B_SH_THEN_W_EX",
            "accepted_writers": 1,
            "second_writer": "REJECTED",
            "maintenance_B_EX": "BLOCKED",
            "classification": "SINGLE_WRITER_ADMISSION_PASS",
        }
    finally:
        if second is not None:
            finish(second)
        finish(first)


def run_suite() -> dict:
    """Run both cases with independent local lock inodes and cleanup."""
    with tempfile.TemporaryDirectory(
        prefix="svt-qa-writer-admission-", dir="/var/tmp"
    ) as folder:
        root = Path(folder)
        root.chmod(0o700)
        a = shared_only_case(root / "shared")
        b = exclusive_writer_case(root / "exclusive")
        return {
            "cases": [a, b],
            "docker_used": False,
            "sqlite_used": False,
            "production_modified": False,
        }


def main() -> int:
    """Inert default, consent-only execution in the fixed offline QA guest."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-disposable", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    if not args.run_disposable:
        print("DRY_RUN: no subprocesses started")
        return 0
    if args.confirm != CONFIRM:
        print("BLOCKED: exact confirmation required")
        return 2
    try:
        load_guard().guest_guard()
        evidence = run_suite()
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print("QA_BLOCKED", type(exc).__name__, str(exc)[:280])
        return 3
    print(
        json.dumps({"status": "PASS_SINGLE_WRITER_ADMISSION_QA", **evidence})
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
