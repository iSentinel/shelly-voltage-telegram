#!/usr/bin/env python3
"""Supervise one already-approved Docker container without an update bypass."""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from writer_fence import (  # noqa: E402
    WriterFenceError,
    fence_directory,
    update_gate,
)

IMAGE = "ghcr.io/isentinel/shelly-voltage-telegram@"
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
STOP = False


class BootBlocked(ValueError):
    """Refuse startup unless exactly one owner and correct image are proven."""


def args_parser() -> argparse.Namespace:
    """Parse approved container and fence paths."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", required=True)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--fence-dir", required=True, type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--qa-offline", action="store_true")
    parser.add_argument("--once", action="store_true")
    return parser.parse_args()


def docker(*argv: str, timeout: int = 15) -> subprocess.CompletedProcess:
    """Run a bounded Docker query without shell interpretation."""
    return subprocess.run(
        ("docker", *argv),
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
    )


def inspect(name: str) -> dict:
    """Read exactly one existing Docker container's configuration."""
    result = docker("inspect", name)
    if result.returncode:
        raise BootBlocked("SVT_CONTAINER_UNAVAILABLE")
    try:
        items = json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        raise BootBlocked("SVT_DOCKER_INSPECT_INVALID") from exc
    if not isinstance(items, list) or len(items) != 1:
        raise BootBlocked("SVT_DOCKER_INSPECT_INVALID")
    return items[0]


def verify_receipt(path: Path | None) -> str:
    """Require a private, single-linked signed release identity file."""
    if path is None:
        raise BootBlocked("SVT_SIGNED_RECEIPT_MISSING")
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        raise BootBlocked("SVT_SIGNED_RECEIPT_UNSAFE")
    record = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(record, dict):
        raise BootBlocked("SVT_SIGNED_RECEIPT_INVALID")
    digest = record.get("digest", "")
    if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
        raise BootBlocked("SVT_SIGNED_RECEIPT_INVALID")
    return IMAGE + digest


def validate_container(info: dict, args: argparse.Namespace) -> None:
    """Require no Docker autostart and exactly the shared fence mount."""
    policy = info.get("HostConfig", {}).get("RestartPolicy", {}).get("Name")
    if policy != "no":
        raise BootBlocked("SVT_DOCKER_RESTART_OWNER_UNFENCED")
    mounts = info.get("Mounts", [])
    shared = [
        mount
        for mount in mounts
        if mount.get("Source") == str(args.fence_dir)
        and mount.get("Destination") == "/run/svt-fence"
        and mount.get("RW") is True
    ]
    if len(shared) != 1:
        raise BootBlocked("SVT_FENCE_MOUNT_MISMATCH")
    if args.qa_offline:
        if not args.container.startswith("svt-qa-"):
            raise BootBlocked("SVT_QA_CONTAINER_UNSAFE")
        if info["HostConfig"].get("NetworkMode") != "none":
            raise BootBlocked("SVT_QA_NETWORK_UNSAFE")
    elif info.get("Config", {}).get("Image") != verify_receipt(args.receipt):
        raise BootBlocked("SVT_SIGNED_IMAGE_RECEIPT_MISMATCH")


def pending(database: Path) -> bool:
    """Reject unsafe marker types while holding the shared update gate."""
    with update_gate(database):
        marker = fence_directory(database) / ".svt-update-pending"
        try:
            info = marker.lstat()
        except FileNotFoundError:
            return False
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != 0
        ):
            raise BootBlocked("SVT_PENDING_MARKER_UNSAFE")
        return True


def wait_container(name: str) -> int:
    """Wait for exit, but close the Docker wait child on a stop signal."""
    child = subprocess.Popen(
        ("docker", "wait", name),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        while child.poll() is None and not STOP:
            time.sleep(0.2)
        if STOP and child.poll() is None:
            child.terminate()
        return child.wait(timeout=5)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def supervise(name: str, database: Path, once: bool) -> int:
    """Restart only after a shared-gate check; stop on any pending update."""
    attempts = 0
    while not STOP:
        if pending(database):
            print("SKIP_PENDING")
            return 0
        started = docker("start", name)
        if started.returncode:
            raise BootBlocked("SVT_DOCKER_START_FAILED")
        print("SUPERVISED_START_PASS", flush=True)
        if once:
            return 0
        result = wait_container(name)
        if STOP:
            return 0
        attempts += 1
        if attempts > 5:
            raise BootBlocked("SVT_RESTART_BUDGET_EXHAUSTED")
        print("SUPERVISED_CONTAINER_EXIT", result, flush=True)
        time.sleep(2)
    return 0


def on_signal(_signum: int, _frame: object) -> None:
    """Prevent launching another writer after supervisor termination."""
    global STOP
    STOP = True


def main() -> int:
    """Validate the sole-owner policy before starting any container."""
    args = args_parser()
    try:
        if os.geteuid() != 0 or not args.database.is_absolute():
            raise BootBlocked("SVT_SUPERVISOR_ROOT_REQUIRED")
        if not args.fence_dir.is_absolute():
            raise BootBlocked("SVT_FENCE_DIR_INVALID")
        os.environ["SVT_WRITER_FENCE_DIR"] = str(args.fence_dir)
        fence_directory(args.database)
        validate_container(inspect(args.container), args)
        signal.signal(signal.SIGTERM, on_signal)
        signal.signal(signal.SIGINT, on_signal)
        return supervise(args.container, args.database, args.once)
    except (BootBlocked, WriterFenceError, OSError, ValueError) as exc:
        code = (
            str(exc)
            if isinstance(exc, (ValueError, WriterFenceError))
            else type(exc).__name__
        )
        print(f"FAIL: {code}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
