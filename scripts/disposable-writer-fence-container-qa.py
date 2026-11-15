#!/usr/bin/env python3
"""QA-only real nonroot Docker writer fence with an invented SQLite file."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

CONFIRM = "OFFLINE_OPTIN_WRITER_FENCE_DOCKER_QA_ONLY"
ORIGINAL = "svt-qa-signed-writer-114"
IMAGE = (
    "sha256:59d8b7c6d7c672958893392dcf09c40da96d664e1b26d6364551e69bcf3a883b"
)
MODULE = Path(__file__).resolve().parents[1] / "writer_fence.py"
WORKER = """
import os
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, "/qa-code")
from writer_fence import (
    WriterFenceError,
    assert_sqlite_writer_fenced,
    writer_fence,
)

mode = sys.argv[1]
marker = Path("/events") / (mode + ".status")
try:
    with writer_fence("required", Path("/locks")):
        assert_sqlite_writer_fenced()
        with sqlite3.connect("/synthetic/qa.sqlite3") as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS evidence(v TEXT)")
            conn.execute("INSERT INTO evidence VALUES(?)", (mode,))
        marker.write_text("RUNNING")
        time.sleep(25)
        marker.write_text("EXITED")
except WriterFenceError:
    marker.write_text("DENIED")
"""


def command(args: list[str], timeout: int = 12) -> subprocess.CompletedProcess:
    """Run a fixed offline Docker preflight or test command."""
    return subprocess.run(
        args, capture_output=True, text=True, check=False, timeout=timeout
    )


def guest_guard() -> None:
    """Refuse any external NIC, running QA writer or enabled QA owner."""
    if os.geteuid() != 0 or socket.gethostname() != "shelly-docker-reboot-qa":
        raise RuntimeError("WRONG_OFFLINE_GUEST")
    if {name for _, name in socket.if_nameindex()} != {"lo"}:
        raise RuntimeError("EXTERNAL_NETWORK_INTERFACE")
    result = command(
        [
            "docker",
            "inspect",
            "--format",
            "{{.State.Running}}|{{.HostConfig.RestartPolicy.Name}}",
            ORIGINAL,
        ]
    )
    if result.returncode or result.stdout.strip() != "false|no":
        raise RuntimeError("ORIGINAL_QA_WRITER_UNSAFE")
    image = command(["docker", "image", "inspect", IMAGE, "-f", "{{.Id}}"])
    if image.returncode or image.stdout.strip() != IMAGE:
        raise RuntimeError("CACHED_IMAGE_MISMATCH")
    for unit in ("svt-qa-compose.service", "svt-qa-guarded-watchdog.timer"):
        if (
            command(["systemctl", "is-enabled", unit]).stdout.strip()
            != "disabled"
        ):
            raise RuntimeError("ORIGINAL_QA_OWNER_ENABLED")


def prepare(root: Path) -> None:
    """Make private root-owned mode and group aware scratch-only mounts."""
    root.chmod(0o700)
    for name, mode in (
        ("locks", 0o750),
        ("code", 0o750),
        ("events", 0o770),
        ("synthetic", 0o770),
    ):
        folder = root / name
        folder.mkdir()
        os.chown(folder, 0, 10001)
        os.chmod(folder, mode)
    for name in ("B", "W"):
        path = root / "locks" / name
        path.touch(mode=0o640, exist_ok=False)
        os.chown(path, 0, 10001)
        os.chmod(path, 0o640)
    target = root / "code" / "writer_fence.py"
    shutil.copyfile(MODULE, target)
    os.chown(target, 0, 10001)
    os.chmod(target, 0o640)


def sandbox_mounts(root: Path) -> list[str]:
    """Mount only synthetic lock files, code, markers and invented SQLite."""
    result: list[str] = []
    for dirname, target, readonly in (
        ("locks", "/locks", True),
        ("code", "/qa-code", True),
        ("events", "/events", False),
        ("synthetic", "/synthetic", False),
    ):
        mount = "type=bind,src=" + str(root / dirname) + ",dst=" + target
        result.extend(["--mount", mount + (",readonly" if readonly else "")])
    return result


def base_flags(name: str) -> list[str]:
    """Return restricted disposable Docker flags."""
    flags = [
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
        "org.svt.qa.fence=" + name,
    ]
    return flags


def arguments(root: Path, name: str, role: str) -> list[str]:
    """Build a constrained no-network Docker invocation."""
    tail = [
        "--env",
        "SHELLY_WRITER_FENCE=required",
        "--entrypoint",
        "python",
        IMAGE,
        "-c",
        WORKER,
        role,
    ]
    return base_flags(name) + sandbox_mounts(root) + tail


def start(root: Path, name: str, role: str) -> None:
    """Start exactly one disposable owner-labelled sandbox container."""
    result = command(arguments(root, name, role), timeout=20)
    if result.returncode:
        raise RuntimeError("QA_SANDBOX_DOCKER_START_FAILED")


def wait_marker(root: Path, role: str, expected: str) -> None:
    """Check a nonroot container event before concluding acceptance."""
    marker = root / "events" / (role + ".status")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if marker.exists():
            data = marker.read_text()
            if data != expected:
                raise RuntimeError("QA_SANDBOX_WRONG_MARKER_" + data)
            return
        time.sleep(0.1)
    raise RuntimeError("QA_SANDBOX_MARKER_TIMEOUT_" + role)


def probe_exclusive(path: Path) -> bool:
    """Try a separate host B/W EX descriptor without retaining it."""
    with path.open("rb") as file:
        try:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True


def owner_label(name: str) -> bool:
    """Verify exact disposable label before stopping or removing."""
    result = command(
        [
            "docker",
            "inspect",
            "-f",
            "{{json .Config.Labels}}",
            name,
        ]
    )
    if result.returncode:
        return False
    if json.loads(result.stdout).get("org.svt.qa.fence") != name:
        raise RuntimeError("DISPOSABLE_OWNER_LABEL_MISMATCH")
    return True


def remove_owned(name: str) -> None:
    """Disable restarts and remove only an exact own test container."""
    if not owner_label(name):
        return
    if command(["docker", "update", "--restart=no", name]).returncode:
        raise RuntimeError("QA_DISABLE_RESTART_FAILED")
    if command(["docker", "rm", "-f", name]).returncode:
        raise RuntimeError("QA_REMOVE_OWNED_FAILED")


def check_writer(root: Path, first: str, second: str) -> dict:
    """Require one SQLite writer and deny a second, then release both locks."""
    b = root / "locks/B"
    w = root / "locks/W"
    start(root, first, "first")
    wait_marker(root, "first", "RUNNING")
    if probe_exclusive(b) or probe_exclusive(w):
        raise RuntimeError("REAL_CONTAINER_LEASE_NOT_HELD")
    start(root, second, "second")
    wait_marker(root, "second", "DENIED")
    db = root / "synthetic/qa.sqlite3"
    if not db.is_file():
        raise RuntimeError("EXPECTED_SYNTHETIC_SQLITE_MISSING")
    if not owner_label(first):
        raise RuntimeError("OWNER_LABEL_MISSING")
    if command(["docker", "kill", "--signal=SIGKILL", first]).returncode:
        raise RuntimeError("QA_WRITER_SIGKILL_FAILED")
    if not probe_exclusive(b) or not probe_exclusive(w):
        raise RuntimeError("LOCKS_STILL_HELD_AFTER_CRASH")
    return {
        "one_writer": "PASS",
        "second_writer": "DENIED",
        "sqlite_created_under_BW": db.is_file(),
        "B_and_W_released_after_SIGKILL": True,
    }


def run_suite() -> dict:
    """Run only a disposable nonroot no-network fixture, always clean."""
    with tempfile.TemporaryDirectory(
        prefix="svt-qa-optin-fence-", dir="/var/tmp"
    ) as path:
        root = Path(path)
        prepare(root)
        token = root.name[-8:]
        blocked, first, second = (
            "svt-qa-fence-blocked-" + token,
            "svt-qa-fence-first-" + token,
            "svt-qa-fence-second-" + token,
        )
        try:
            with (root / "locks/B").open("rb") as owner:
                fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
                start(root, blocked, "blocked")
                wait_marker(root, "blocked", "DENIED")
            proof = check_writer(root, first, second)
            return {
                "host_B_EX_denies_before_sqlite": True,
                **proof,
                "production_modified": False,
            }
        finally:
            for name in (second, first, blocked):
                remove_owned(name)


def main() -> int:
    """Opt-in only on exact offline LXC114; default has no side effects."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-disposable", action="store_true")
    parser.add_argument("--confirm", default="")
    options = parser.parse_args()
    if not options.run_disposable:
        print("DRY_RUN: no Docker or SQLite actions")
        return 0
    if options.confirm != CONFIRM:
        print("BLOCKED: exact offline QA consent required")
        return 2
    try:
        guest_guard()
        evidence = run_suite()
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print("QA_BLOCKED", type(exc).__name__, str(exc)[:260])
        return 3
    print(
        json.dumps({"result": "PASS_OPTIN_FENCE_NONROOT_DOCKER", **evidence})
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
