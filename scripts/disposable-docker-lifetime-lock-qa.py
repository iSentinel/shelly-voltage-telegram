#!/usr/bin/env python3
"""Reproduce detached Docker lifetime-lock gap on isolated LXC114."""

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

OWNER_PATH = (
    Path(__file__).resolve().with_name("disposable-docker-restart-owner-qa.py")
)
CONFIRM = "OFFLINE_DOCKER_LIFETIME_LOCK_GAP_QA_ONLY"
WORKER = "import time; time.sleep(50)"


def import_owner() -> object:
    """Load only the QA script beside this executable."""
    spec = importlib.util.spec_from_file_location(
        "qa_owner_lifetime", OWNER_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("QA_OWNER_MODULE_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(command: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    """Run one bounded local QA subprocess."""
    return subprocess.run(
        command, capture_output=True, text=True, check=False, timeout=timeout
    )


def prepare(root: Path) -> None:
    """Create a private B lock and a writeable inert counter path."""
    root.chmod(0o700)
    events = root / "events"
    events.mkdir(mode=0o700)
    os.chown(events, 10001, 10001)
    (root / "maintenance.lock").touch(mode=0o600, exist_ok=False)


def args_for_writer(owner: object, root: Path, name: str) -> list[str]:
    """Use the existing resource-bounded no-network QA Docker arguments."""
    command = owner.container_args(root / "events", name, "no")
    if command[-1] != owner.WORKER or command[-2] != "-c":
        raise RuntimeError("UNKNOWN_DOCKER_QA_COMMAND")
    return [*command[:-1], WORKER]


def run_detached_unit(
    owner: object, root: Path, name: str, tag: str
) -> subprocess.CompletedProcess:
    """Protect a real systemd start with B SH only until docker -d exits."""
    args = args_for_writer(owner, root, name)
    command = [
        "systemd-run",
        "--unit=svt-qa-docker-life-" + tag,
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
        "ReadWritePaths=" + str(root),
        "/usr/bin/flock",
        "--shared",
        "--nonblock",
        str(root / "maintenance.lock"),
        "/usr/bin/docker",
        *args[1:],
    ]
    return run(command)


def running(owner: object, name: str) -> bool:
    """Read the current state of the exact disposable labelled Docker."""
    result = owner.command(
        [
            "docker",
            "inspect",
            "--format",
            "{{.State.Running}}|{{.HostConfig.RestartPolicy.Name}}",
            name,
        ]
    )
    return result.returncode == 0 and result.stdout.strip() == "true|no"


def acquire_exclusive(root: Path) -> bool:
    """Attempt maintenance B EX without waiting for an active reader."""
    with (root / "maintenance.lock").open("rb") as file:
        try:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True


def start_wait_lease(root: Path, name: str) -> subprocess.Popen:
    """Hold shared B as long as a real Docker wait client is alive."""
    return subprocess.Popen(
        [
            "/usr/bin/flock",
            "--shared",
            "--nonblock",
            str(root / "maintenance.lock"),
            "/usr/bin/docker",
            "wait",
            name,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def require_lease(root: Path, lease: subprocess.Popen) -> None:
    """Ensure B EX is refused by the independently running wait client."""
    deadline = time.monotonic() + 9
    while time.monotonic() < deadline:
        if lease.poll() is not None:
            raise RuntimeError("LEASE_TERMINATED_BEFORE_DOCKER")
        if not acquire_exclusive(root):
            return
        time.sleep(0.1)
    raise RuntimeError("LEASE_NEVER_HELD_SHARED_LOCK")


def finish_lease(lease: subprocess.Popen) -> None:
    """Reap the exact synthetic supervisor to prevent stray QA processes."""
    try:
        lease.communicate(timeout=6)
    except subprocess.TimeoutExpired:
        lease.terminate()
        try:
            lease.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            lease.kill()
            lease.communicate(timeout=3)


def perform_qa(owner: object) -> dict:
    """Prove start-only B SH gap, then a separately held wait lease."""
    with tempfile.TemporaryDirectory(
        prefix="svt-qa-docker-lifetime-", dir="/var/tmp"
    ) as raw:
        root = Path(raw)
        prepare(root)
        name = "svt-qa-life-" + root.name[-8:]
        unit = str(os.getpid()) + "-" + root.name[-8:]
        lease = None
        try:
            result = run_detached_unit(owner, root, name, unit)
            if result.returncode or not running(owner, name):
                raise RuntimeError("GUARDED_DOCKER_START_FAILED")
            if not acquire_exclusive(root) or not running(owner, name):
                raise RuntimeError("START_ONLY_LOCK_GAP_NOT_REPRODUCED")
            lease = start_wait_lease(root, name)
            require_lease(root, lease)
            if not running(owner, name) or acquire_exclusive(root):
                raise RuntimeError("LIFETIME_SHARED_LEASE_FAILED")
            stopped = owner.command(["docker", "stop", "-t", "1", name])
            if stopped.returncode:
                raise RuntimeError("SYNTHETIC_STOP_FAILED")
            finish_lease(lease)
            if not acquire_exclusive(root):
                raise RuntimeError("LEASE_NOT_RELEASED_AFTER_STOP")
            return {
                "detached_start_lock_gap": "B_EX_ACQUIRED_WHILE_RUNNING",
                "docker_wait_lease": "B_EX_REFUSED_WHILE_RUNNING",
                "after_stop": "B_EX_ACQUIRED",
                "docker_restart_policy": "no",
                "production_modified": False,
            }
        finally:
            if lease is not None:
                if lease.poll() is None:
                    owner.command(["docker", "stop", "-t", "1", name])
                finish_lease(lease)
            owner.cleanup_container(name)


def main() -> int:
    """Default inert; exact confirmation and LXC114 guest safety gate."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-disposable", action="store_true")
    parser.add_argument("--confirm", default="")
    options = parser.parse_args()
    if not options.run_disposable:
        print("DRY_RUN: no Docker container or systemd unit started")
        return 0
    if options.confirm != CONFIRM:
        print("BLOCKED: exact isolated QA confirmation required")
        return 2
    try:
        owner = import_owner()
        owner.guest_guard()
        proof = perform_qa(owner)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print("QA_BLOCKED", type(exc).__name__, str(exc)[:350])
        return 3
    print(
        json.dumps(
            {
                "result": "PASS_DOCKER_LIFETIME_LOCK_GAP",
                "evidence": proof,
                "signature_trust": "CACHED_ONLY",
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
