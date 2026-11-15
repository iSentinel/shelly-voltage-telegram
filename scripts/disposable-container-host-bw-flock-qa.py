#!/usr/bin/env python3
"""Check host-visible B SH and W EX inside networkless QA Docker."""

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
CONFIRM = "OFFLINE_CONTAINER_HOST_BW_LOCK_QA_ONLY"
ORIGINAL = "svt-qa-signed-writer-114"
WORKER = """
import fcntl
import sys
import time
from pathlib import Path

mode = sys.argv[1]
marker = Path("/events") / (mode + ".status")
with Path("/locks/B").open("rb") as B:
    try:
        fcntl.flock(B, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except BlockingIOError:
        marker.write_text("DENIED_B")
        raise SystemExit(31)
    with Path("/locks/W").open("rb") as W:
        try:
            fcntl.flock(W, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            marker.write_text("DENIED_W")
            raise SystemExit(32)
        marker.write_text("RUNNING")
        time.sleep(25)
        marker.write_text("EXITED")
"""


def call(args: list[str], timeout: int = 12) -> subprocess.CompletedProcess:
    """Run one fixed local Docker inspection/QA command with deadline."""
    return subprocess.run(
        args, capture_output=True, text=True, timeout=timeout, check=False
    )


def guest_guard() -> None:
    """Refuse all QA outside stopped-writer networkless LXC114."""
    if os.geteuid() != 0 or socket.gethostname() != "shelly-docker-reboot-qa":
        raise RuntimeError("NOT_EXACT_OFFLINE_QA")
    if {name for _, name in socket.if_nameindex()} != {"lo"}:
        raise RuntimeError("EXTERNAL_NETWORK_INTERFACE")
    status = call(
        [
            "docker",
            "inspect",
            "--format",
            "{{.State.Running}}|{{.HostConfig.RestartPolicy.Name}}",
            ORIGINAL,
        ]
    )
    if status.returncode or status.stdout.strip() != "false|no":
        raise RuntimeError("ORIGINAL_QA_WRITER_UNSAFE")
    image = call(["docker", "image", "inspect", IMAGE, "--format", "{{.Id}}"])
    if image.returncode or image.stdout.strip() != IMAGE:
        raise RuntimeError("CACHED_IMAGE_ID_MISMATCH")
    for unit in ("svt-qa-compose.service", "svt-qa-guarded-watchdog.timer"):
        state = call(["systemctl", "is-enabled", unit])
        if state.stdout.strip() != "disabled":
            raise RuntimeError("ORIGINAL_QA_SYSTEMD_OWNER_ENABLED")


def prepare(root: Path) -> None:
    """Create isolated root-owned 0750 group-accessible lock mounts."""
    root.chmod(0o700)
    locks = root / "locks"
    events = root / "events"
    locks.mkdir(mode=0o750)
    events.mkdir(mode=0o770)
    for directory, mode in ((locks, 0o750), (events, 0o770)):
        os.chown(directory, 0, 10001)
        os.chmod(directory, mode)
    for filename in ("B", "W"):
        path = locks / filename
        path.touch(mode=0o640, exist_ok=False)
        os.chown(path, 0, 10001)
        os.chmod(path, 0o640)


def docker_args(root: Path, name: str, mode: str) -> list[str]:
    """Create only a nonroot labelled cached-image sandbox container."""
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
        "no",
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
        "--label",
        "org.svt.qa.lockowner=" + name,
        "--mount",
        "type=bind,src=" + str(root / "locks") + ",dst=/locks,readonly",
        "--mount",
        "type=bind,src=" + str(root / "events") + ",dst=/events",
        "--entrypoint",
        "python",
        IMAGE,
        "-c",
        WORKER,
        mode,
    ]


def start(root: Path, name: str, mode: str) -> None:
    """Start only the disposable sandbox without network or restart."""
    result = call(docker_args(root, name, mode), timeout=25)
    if result.returncode:
        raise RuntimeError("SANDBOX_DOCKER_START_FAILED_" + mode)


def state(name: str) -> tuple[bool, str]:
    """Read exact disposable running state and restart policy."""
    result = call(
        [
            "docker",
            "inspect",
            "--format",
            "{{.State.Running}}|{{.HostConfig.RestartPolicy.Name}}",
            name,
        ]
    )
    if result.returncode:
        raise RuntimeError("SANDBOX_DOCKER_INSPECT_FAILED")
    running, policy = result.stdout.strip().split("|")
    if policy != "no":
        raise RuntimeError("SANDBOX_RESTART_POLICY_UNEXPECTED")
    return running == "true", policy


def wait_for(root: Path, name: str, expected: str) -> None:
    """Require exact sandbox marker within bounded time."""
    path = root / "events" / (name + ".status")
    deadline = time.monotonic() + 9
    while time.monotonic() < deadline:
        if path.exists():
            content = path.read_text()
            if content == expected:
                return
            raise RuntimeError("UNEXPECTED_SANDBOX_MARKER_" + content)
        time.sleep(0.08)
    raise RuntimeError("SANDBOX_MARKER_TIMEOUT_" + name)


def can_get_exclusive(path: Path) -> bool:
    """Probe a fresh host FD against the container's lock inode."""
    with path.open("rb") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True


def verify_ownership(name: str) -> bool:
    """Check a disposable container label before any cleanup/kill."""
    result = call(
        [
            "docker",
            "inspect",
            "--format",
            "{{json .Config.Labels}}",
            name,
        ]
    )
    if result.returncode:
        return False
    labels = json.loads(result.stdout)
    if labels.get("org.svt.qa.lockowner") != name:
        raise RuntimeError("UNKNOWN_CONTAINER_OWNER")
    return True


def remove_owned(name: str) -> None:
    """Disable restart and remove only a verified disposable QA container."""
    if not verify_ownership(name):
        return
    if call(["docker", "update", "--restart=no", name]).returncode:
        raise RuntimeError("SANDBOX_DISABLE_RESTART_FAILED")
    if call(["docker", "rm", "-f", name]).returncode:
        raise RuntimeError("SANDBOX_REMOVE_FAILED")


def crash_owned(name: str) -> None:
    """SIGKILL only the exact labelled disposable QA Docker container."""
    if not verify_ownership(name):
        raise RuntimeError("SANDBOX_NOT_FOUND_FOR_KILL")
    if call(["docker", "kill", "--signal=SIGKILL", name]).returncode:
        raise RuntimeError("SANDBOX_KILL_FAILED")


def check_container_locks(
    root: Path, B: Path, W: Path, names: tuple[str, str, str]
) -> dict:
    """Verify real container B/W lock isolation and synthetic crash release."""
    blocked, first, second = names
    with B.open("rb") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        start(root, blocked, "blocked")
        wait_for(root, "blocked", "DENIED_B")
    start(root, first, "first")
    wait_for(root, "first", "RUNNING")
    if not state(first)[0]:
        raise RuntimeError("FIRST_WRITER_NOT_RUNNING")
    if can_get_exclusive(B) or can_get_exclusive(W):
        raise RuntimeError("HOST_LOCK_NOT_HELD_BY_CONTAINER")
    start(root, second, "second")
    wait_for(root, "second", "DENIED_W")
    if not state(first)[0]:
        raise RuntimeError("FIRST_WRITER_STOPPED_TOO_EARLY")
    crash_owned(first)
    if not can_get_exclusive(B) or not can_get_exclusive(W):
        raise RuntimeError("HOST_LOCKS_NOT_RELEASED_AFTER_CRASH")
    return {
        "host_B_EX_blocks_container_writer": True,
        "container_B_SH_prevents_host_maintenance": True,
        "container_W_EX_rejects_second_writer": True,
        "post_SIGKILL_B_and_W_released": True,
        "signature_trust": "CACHED_IMAGE_ID_ONLY",
        "production_modified": False,
    }


def run_suite() -> dict:
    """Run one exact isolated Docker fixture and always clean owned objects."""
    with tempfile.TemporaryDirectory(
        prefix="svt-qa-container-bw-", dir="/var/tmp"
    ) as raw:
        root = Path(raw)
        prepare(root)
        token = root.name[-8:]
        names = (
            "svt-qa-bw-B-" + token,
            "svt-qa-bw-first-" + token,
            "svt-qa-bw-second-" + token,
        )
        try:
            return check_container_locks(
                root, root / "locks/B", root / "locks/W", names
            )
        finally:
            for name in reversed(names):
                remove_owned(name)


def main() -> int:
    """Default inert; exact consent and isolated guest gate required."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-disposable", action="store_true")
    parser.add_argument("--confirm", default="")
    options = parser.parse_args()
    if not options.run_disposable:
        print("DRY_RUN: no Docker process or file changed")
        return 0
    if options.confirm != CONFIRM:
        print("BLOCKED: exact offline consent required")
        return 2
    try:
        guest_guard()
        proof = run_suite()
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print("QA_BLOCKED", type(error).__name__, str(error)[:280])
        return 3
    print(json.dumps({"result": "PASS_CONTAINER_HOST_BW_FLOCK", **proof}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
