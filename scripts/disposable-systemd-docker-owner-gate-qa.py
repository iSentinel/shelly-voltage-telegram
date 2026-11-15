#!/usr/bin/env python3
"""Rehearse a real systemd B-SH-guarded Docker start on offline LXC114."""

from __future__ import annotations

import argparse
import fcntl
import importlib.util
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

OWNER = (
    Path(__file__).resolve().with_name("disposable-docker-restart-owner-qa.py")
)
CONFIRM = "OFFLINE_SYSTEMD_DOCKER_B_GUARD_QA_ONLY"


def load_owner() -> object:
    """Import the fixed offline Docker QA owner guard from this repository."""
    spec = importlib.util.spec_from_file_location("qa_owner", OWNER)
    if spec is None or spec.loader is None:
        raise RuntimeError("QA_OWNER_MODULE_MISSING")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def launch_unit(
    owner: object, folder: Path, name: str, suffix: str
) -> subprocess.CompletedProcess:
    """Hold B SH through a real transient systemd Docker start."""
    docker_args = owner.container_args(folder / "events", name, "no")
    command = [
        "systemd-run",
        "--unit=svt-qa-docker-b-" + suffix,
        "--wait",
        "--pipe",
        "--collect",
        "-p",
        "Type=oneshot",
        "-p",
        "PrivateNetwork=yes",
        "-p",
        "ProtectSystem=strict",
        "-p",
        "ReadWritePaths=" + str(folder),
        "/usr/bin/flock",
        "--shared",
        "--nonblock",
        str(folder / "maintenance.lock"),
        "/usr/bin/docker",
        *docker_args[1:],
    ]
    return subprocess.run(
        command, check=False, capture_output=True, text=True, timeout=35
    )


def await_exited(owner: object, folder: Path, name: str) -> dict:
    """Observe one inert container exit and zero automatic restarts."""
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        count, state = owner.get_state(name, "no")
        target = folder / "events/restarts.log"
        attempts = (
            len(target.read_text().splitlines()) if target.exists() else 0
        )
        if count == 0 and state == "exited" and attempts == 1:
            return {
                "docker_restart": "no",
                "restart_count": count,
                "synthetic_attempts": attempts,
                "state": state,
            }
        time.sleep(0.2)
    raise RuntimeError("ONE_SHOT_DOCKER_COMPLETION_MISSING")


def prepare(folder: Path) -> None:
    """Create only disposable root-owned B lock and nonroot counter path."""
    folder.chmod(0o700)
    events = folder / "events"
    events.mkdir(mode=0o700)
    os.chown(events, 10001, 10001)
    (folder / "maintenance.lock").touch(mode=0o600, exist_ok=False)


def rehearse(owner: object) -> dict:
    """Prove B EX denies a systemd Docker start, B free permits one."""
    with tempfile.TemporaryDirectory(
        prefix="svt-qa-guarded-docker-", dir="/var/tmp"
    ) as raw:
        folder = Path(raw)
        prepare(folder)
        name = "svt-qa-guarded-" + folder.name[-8:]
        unit_base = str(os.getpid()) + "-" + folder.name[-8:]
        try:
            with (folder / "maintenance.lock").open("rb") as holder:
                fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
                denied = launch_unit(owner, folder, name, unit_base + "-deny")
                existing = owner.command(["docker", "inspect", name])
                if denied.returncode == 0 or existing.returncode == 0:
                    raise RuntimeError("B_EX_ALLOWED_UNEXPECTED_DOCKER_START")
            allowed = launch_unit(owner, folder, name, unit_base + "-allow")
            if allowed.returncode:
                raise RuntimeError(
                    "B_FREE_START_FAILED: " + allowed.stderr[-250:]
                )
            observed = await_exited(owner, folder, name)
            return {
                "B_EX_denied_systemd_start": True,
                "B_unlocked_allowed_systemd_start": True,
                "docker": observed,
                "trust": "CACHED_LOCAL_IMAGE_ONLY",
                "production_modified": False,
            }
        finally:
            owner.cleanup_container(name)


def main() -> int:
    """Default inert; only exact consent and stopped-writer QA guest run."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-disposable", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    if not args.run_disposable:
        print("DRY_RUN: no systemd unit or Docker container touched")
        return 0
    if args.confirm != CONFIRM:
        print("BLOCKED: exact QA confirmation required")
        return 2
    try:
        owner = load_owner()
        owner.guest_guard()
        results = rehearse(owner)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print("QA_BLOCKED", type(exc).__name__, str(exc)[:320])
        return 3
    print(
        json.dumps(
            {
                "result": "PASS_REAL_SYSTEMD_B_SH_DOCKER_GATE",
                "evidence": results,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
