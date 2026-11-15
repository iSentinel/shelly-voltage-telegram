#!/usr/bin/env python3
"""Reproduce lock inode replacement bypass in private offline QA."""

from __future__ import annotations

import argparse
import fcntl
import importlib.util
import json
import os
import subprocess
import tempfile
from pathlib import Path

GUARD_PATH = (
    Path(__file__).resolve().with_name("disposable-inherited-b-lifetime-qa.py")
)
CONFIRM = "OFFLINE_BW_INODE_REPLACEMENT_QA_ONLY"


def load_guard() -> object:
    """Import only the sibling LXC114 offline guest guard."""
    spec = importlib.util.spec_from_file_location(
        "qa_inherited_guard", GUARD_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("EXACT_QA_GUARD_MISSING")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def probe_exclusive(path: Path) -> bool:
    """Try acquiring an independent B/W EX lock in another real process."""
    result = subprocess.run(
        [
            "/usr/bin/flock",
            "--exclusive",
            "--nonblock",
            str(path),
            "/usr/bin/true",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    return result.returncode == 0


def replace_inode(path: Path) -> tuple[int, int]:
    """Replace only a fresh private QA lock inode, never a production file."""
    before = path.stat().st_ino
    target = path.with_suffix(".replacement")
    target.touch(mode=0o600, exist_ok=False)
    os.chmod(target, 0o600)
    os.replace(target, path)
    after = path.stat().st_ino
    if before == after:
        raise RuntimeError("TEST_INODE_WAS_NOT_REPLACED")
    return before, after


def check_case(root: Path) -> dict:
    """Show separate EX probes bypass still-held old B SH and W EX."""
    B = root / "maintenance.lock"
    W = root / "writer.lock"
    B.touch(mode=0o600)
    W.touch(mode=0o600)
    with B.open("rb") as holder_B, W.open("rb") as holder_W:
        fcntl.flock(holder_B, fcntl.LOCK_SH | fcntl.LOCK_NB)
        fcntl.flock(holder_W, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if probe_exclusive(B) or probe_exclusive(W):
            raise RuntimeError("OLD_INODE_LOCK_NOT_EFFECTIVE")
        before_B, after_B = replace_inode(B)
        before_W, after_W = replace_inode(W)
        if not probe_exclusive(B) or not probe_exclusive(W):
            raise RuntimeError("INODE_REPLACEMENT_BYPASS_NOT_OBSERVED")
        return {
            "old_B_inode": before_B,
            "new_B_inode": after_B,
            "old_W_inode": before_W,
            "new_W_inode": after_W,
            "old_held_B_SH": True,
            "old_held_W_EX": True,
            "new_B_EX_during_old_B_SH": "ALLOWED_GAP",
            "new_W_EX_during_old_W_EX": "ALLOWED_GAP",
            "classification": "LOCK_INODE_REPLACEMENT_SPLIT_BRAIN",
        }


def run_suite() -> dict:
    """Contain the inode swap inside one root-only disposable QA tree."""
    with tempfile.TemporaryDirectory(
        prefix="svt-qa-inode-swap-", dir="/var/tmp"
    ) as name:
        root = Path(name)
        root.chmod(0o700)
        result = check_case(root)
        return {
            "case": result,
            "docker_used": False,
            "production_modified": False,
        }


def main() -> int:
    """Inert default; exact offline guest and confirmation only."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-disposable", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    if not args.run_disposable:
        print("DRY_RUN: no file created or lock opened")
        return 0
    if args.confirm != CONFIRM:
        print("BLOCKED: exact isolated QA confirmation required")
        return 2
    try:
        load_guard().guest_guard()
        result = run_suite()
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print("QA_BLOCKED", type(exc).__name__, str(exc)[:250])
        return 3
    print(json.dumps({"status": "PASS_BW_INODE_SWAP_QA", **result}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
