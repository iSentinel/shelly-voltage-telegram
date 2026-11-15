#!/usr/bin/env python3
"""Demonstrate that Docker restart can bypass an advisory QA B lock."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path

IMAGE = (
    "sha256:59d8b7c6d7c672958893392dcf09c40da96d664e1b26d6364551e69bcf3a883b"
)
WRITER = "svt-qa-signed-writer-114"
CONFIRM = "OFFLINE_DOCKER_RESTART_OWNER_BYPASS_QA_ONLY"
UNITS = ("svt-qa-compose.service", "svt-qa-guarded-watchdog.timer")
WORKER = """
from pathlib import Path
import os
import sys
import time

path = Path("/work/restarts.log")
with path.open("a", encoding="ascii") as file:
    file.write("attempt\\n")
    file.flush()
    os.fsync(file.fileno())
attempt = len(path.read_text(encoding="ascii").splitlines())
if attempt < 3:
    sys.exit(44)
time.sleep(90)
"""


def command(args: list[str], timeout: int = 12) -> subprocess.CompletedProcess:
    """Run a fixed local QA command with a firm deadline."""
    return subprocess.run(
        args, capture_output=True, text=True, check=False, timeout=timeout
    )


def guest_guard() -> None:
    """Refuse real Docker experiments outside isolated stopped-writer QA."""
    if os.geteuid() != 0 or socket.gethostname() != "shelly-docker-reboot-qa":
        raise RuntimeError("NOT_LXC114_QA")
    if {name for _, name in socket.if_nameindex()} != {"lo"}:
        raise RuntimeError("QA_NETWORK_NOT_ISOLATED")
    original = command(
        [
            "docker",
            "inspect",
            "--format",
            "{{.State.Running}}|{{.HostConfig.RestartPolicy.Name}}",
            WRITER,
        ]
    )
    if original.returncode or original.stdout.strip() != "false|no":
        raise RuntimeError("ORIGINAL_QA_WRITER_UNSAFE")
    image = command(
        ["docker", "image", "inspect", IMAGE, "--format", "{{.Id}}"]
    )
    if image.returncode or image.stdout.strip() != IMAGE:
        raise RuntimeError("QA_IMAGE_ID_MISMATCH")
    for unit in UNITS:
        result = command(["systemctl", "is-enabled", unit], timeout=5)
        if result.stdout.strip() != "disabled":
            raise RuntimeError("ORIGINAL_QA_OWNER_ENABLED")


def container_args(directory: Path, name: str) -> list[str]:
    """Use only a local signed-image copy and private counter mount."""
    return [
        "docker",
        "run",
        "-d",
        "--name",
        name,
        "--pull",
        "never",
        "--network",
        "none",
        "--restart",
        "unless-stopped",
        "--label",
        "org.svt.qa.owner=" + name,
        "--read-only",
        "--user",
        "10001:10001",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "32",
        "--memory",
        "128m",
        "--cpus",
        "0.25",
        "--mount",
        "type=bind,src=" + str(directory) + ",dst=/work",
        "--entrypoint",
        "python",
        IMAGE,
        "-c",
        WORKER,
    ]


def get_state(name: str) -> tuple[int, str]:
    """Read Docker restart count and current runtime state."""
    result = command(
        [
            "docker",
            "inspect",
            "--format",
            "{{.RestartCount}}|{{.State.Status}}|"
            "{{.HostConfig.RestartPolicy.Name}}",
            name,
        ]
    )
    if result.returncode:
        raise RuntimeError("QA_CONTAINER_INSPECT_FAILED")
    count, state, policy = result.stdout.strip().split("|")
    if policy != "unless-stopped":
        raise RuntimeError("RESTART_POLICY_CHANGED")
    return int(count), state


def prove_bypass(directory: Path, name: str) -> dict:
    """Keep B exclusive while Docker independently restarts a dummy."""
    started = command(container_args(directory, name), timeout=25)
    if started.returncode:
        raise RuntimeError("QA_DOCKER_RUN_FAILED: " + started.stderr[:300])
    deadline = time.monotonic() + 18
    while time.monotonic() < deadline:
        count, state = get_state(name)
        events = directory / "restarts.log"
        attempts = (
            len(events.read_text().splitlines()) if events.exists() else 0
        )
        if count >= 2 and attempts >= 3 and state == "running":
            return {
                "restart_policy": "unless-stopped",
                "restart_count": count,
                "attempts_under_B_EX": attempts,
                "writer": "INERT_SYNTHETIC_COUNTER_ONLY",
                "maintenance_B": "EXCLUSIVE_HELD",
                "classification": "BYPASS_CONFIRMED",
            }
        time.sleep(0.35)
    raise RuntimeError("RESTART_BYPASS_NOT_OBSERVED")


def cleanup_container(name: str) -> None:
    """Remove only the exact disposable container owned by this test."""
    result = command(
        ["docker", "inspect", "--format", "{{json .Config.Labels}}", name]
    )
    if result.returncode:
        return
    labels = json.loads(result.stdout)
    if labels.get("org.svt.qa.owner") != name:
        raise RuntimeError("UNEXPECTED_CONTAINER_OWNERSHIP")
    disabled = command(["docker", "update", "--restart=no", name])
    removed = command(["docker", "rm", "-f", name])
    if disabled.returncode or removed.returncode:
        raise RuntimeError("QA_CONTAINER_CLEANUP_FAILED")
    if command(["docker", "inspect", name]).returncode == 0:
        raise RuntimeError("QA_CONTAINER_NOT_REMOVED")


def run_disposable() -> dict:
    """Create two private lock/counter paths, then delete everything."""
    with tempfile.TemporaryDirectory(
        prefix="svt-docker-owner-qa-", dir="/var/tmp"
    ) as raw:
        folder = Path(raw)
        folder.chmod(0o700)
        events = folder / "events"
        events.mkdir(mode=0o700)
        os.chown(events, 10001, 10001)
        maintenance = folder / "maintenance.lock"
        maintenance.touch(mode=0o600, exist_ok=False)
        name = "svt-qa-owner-" + folder.name[-8:]
        with maintenance.open("rb") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                return prove_bypass(events, name)
            finally:
                cleanup_container(name)


def main() -> int:
    """Remain inert unless the exact isolated QA consent is supplied."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-disposable", action="store_true")
    parser.add_argument("--confirm", default="")
    arguments = parser.parse_args()
    if not arguments.run_disposable:
        print("DRY_RUN: no Docker containers or locks touched")
        return 0
    if arguments.confirm != CONFIRM:
        print("BLOCKED: exact confirmation required")
        return 2
    try:
        guest_guard()
        proof = run_disposable()
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print("QA_BLOCKED", type(error).__name__, str(error)[:400])
        return 3
    print(
        json.dumps(
            {
                "result": "PASS_DOCKER_RESTART_BYPASS_QA",
                "evidence": proof,
                "production_modified": False,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
