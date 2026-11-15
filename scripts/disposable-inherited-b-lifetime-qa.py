#!/usr/bin/env python3
"""Check synthetic writer lock inheritance after supervisor SIGKILL."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import suppress
from pathlib import Path

CONFIRM = "OFFLINE_INHERITED_B_LIFETIME_QA_ONLY"
PREFIX = "svt-qa-inherited-b-"

WORKER_CODE = """
import sys
import time
from pathlib import Path

marker = Path(sys.argv[1])
marker.write_text("RUNNING")
time.sleep(3.0)
marker.write_text("EXITED")
"""

SUPERVISOR_CODE = """
import fcntl
import os
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])
mode = sys.argv[2]
if mode not in ("inherited", "parent_only"):
    raise SystemExit(7)
fd = os.open(root / "maintenance.lock", os.O_RDONLY)
fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
pass_fds = (fd,) if mode == "inherited" else ()
child = subprocess.Popen(
    [sys.executable, "-c", sys.argv[3], str(root / "marker")],
    pass_fds=pass_fds, stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
(root / "child.pid").write_text(str(child.pid))
(root / "supervisor.ready").write_text("READY")
child.wait()
"""


def cmd(args: list[str]) -> subprocess.CompletedProcess:
    """Run one fixed read-only QA preflight with a strict timeout."""
    return subprocess.run(
        args, capture_output=True, text=True, check=False, timeout=8
    )


def guest_guard() -> None:
    """Refuse experiments outside exact loopback-only stopped-writer LXC114."""
    if os.geteuid() != 0 or socket.gethostname() != "shelly-docker-reboot-qa":
        raise RuntimeError("NOT_EXACT_OFFLINE_QA")
    if {x for _, x in socket.if_nameindex()} != {"lo"}:
        raise RuntimeError("EXTERNAL_INTERFACE_PRESENT")
    state = cmd(
        [
            "docker",
            "inspect",
            "--format",
            "{{.State.Running}}|{{.HostConfig.RestartPolicy.Name}}",
            "svt-qa-signed-writer-114",
        ]
    )
    if state.returncode or state.stdout.strip() != "false|no":
        raise RuntimeError("ORIGINAL_QA_WRITER_UNSAFE")
    units = ("svt-qa-compose.service", "svt-qa-guarded-watchdog.timer")
    for unit in units:
        result = cmd(["systemctl", "is-enabled", unit])
        if result.stdout.strip() != "disabled":
            raise RuntimeError("ORIGINAL_QA_SYSTEMD_OWNER_ENABLED")


def can_get_exclusive(root: Path) -> bool:
    """Probe a separate file descriptor without holding the lock afterward."""
    with (root / "maintenance.lock").open("rb") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True


def wait_marker(root: Path, expected: str, deadline: float) -> None:
    """Wait only for synthetic scratch marker with bounded elapsed time."""
    marker = root / "marker"
    while time.monotonic() < deadline:
        if marker.is_file() and marker.read_text() == expected:
            return
        time.sleep(0.03)
    raise RuntimeError("WORKER_MARKER_TIMEOUT_" + expected)


def wait_supervisor(root: Path, parent: subprocess.Popen) -> int:
    """Ensure fixed synthetic worker and supervisor are both started."""
    end = time.monotonic() + 6
    while time.monotonic() < end:
        ready = root / "supervisor.ready"
        pidpath = root / "child.pid"
        if ready.is_file() and pidpath.is_file():
            pid = int(pidpath.read_text())
            if pid <= 1:
                raise RuntimeError("INVALID_SYNTHETIC_CHILD_PID")
            wait_marker(root, "RUNNING", end)
            return pid
        if parent.poll() is not None:
            raise RuntimeError("SUPERVISOR_EXITED_BEFORE_HANDSHAKE")
        time.sleep(0.03)
    raise RuntimeError("SUPERVISOR_HANDSHAKE_TIMEOUT")


def reap_supervisor(parent: subprocess.Popen) -> None:
    """Always reap the exact child supervisor, including killed processes."""
    if parent.poll() is None:
        parent.kill()
    parent.wait(timeout=4)


def wait_exclusive_release(root: Path, deadline: float) -> None:
    """Prove B EX eventually returns after synthetic worker completion."""
    while time.monotonic() < deadline:
        if can_get_exclusive(root):
            return
        time.sleep(0.03)
    raise RuntimeError("B_EX_NOT_RELEASED_AFTER_WORKER_EXIT")


def spawn_supervisor(root: Path, mode: str) -> subprocess.Popen:
    """Start only a synthetic child tree in a new private directory."""
    root.mkdir(mode=0o700)
    (root / "maintenance.lock").touch(mode=0o600)
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            SUPERVISOR_CODE,
            str(root),
            mode,
            WORKER_CODE,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def cleanup_case(
    root: Path, parent: subprocess.Popen, child: int | None
) -> None:
    """Reap only the spawned supervisor and bounded synthetic child."""
    reap_supervisor(parent)
    marker = root / "marker"
    if child is not None and (
        not marker.exists() or marker.read_text() != "EXITED"
    ):
        with suppress(ProcessLookupError):
            os.kill(child, signal.SIGTERM)


def case(root: Path, mode: str) -> dict:
    """Kill supervisor and verify inherited lifetime lock survives."""
    process = spawn_supervisor(root, mode)
    worker = None
    try:
        worker = wait_supervisor(root, process)
        if can_get_exclusive(root):
            raise RuntimeError("B_EX_BEFORE_SUPERVISOR_DEATH")
        process.send_signal(signal.SIGKILL)
        process.wait(timeout=4)
        still_running = (root / "marker").read_text() == "RUNNING"
        if not still_running:
            raise RuntimeError("SYNTHETIC_WORKER_ALREADY_EXITED")
        after = can_get_exclusive(root)
        if after == (mode == "inherited"):
            raise RuntimeError("UNEXPECTED_B_EX_AFTER_SUPERVISOR_KILL")
        wait_marker(root, "EXITED", time.monotonic() + 7)
        wait_exclusive_release(root, time.monotonic() + 5)
        return {
            "mode": mode,
            "supervisor": "KILLED_SIGKILL",
            "synthetic_writer_running_after_supervisor_kill": True,
            "maintenance_B_EX_while_writer_running": (
                "BLOCKED" if mode == "inherited" else "ALLOWED_GAP"
            ),
            "maintenance_B_EX_after_writer_exit": "ALLOWED",
        }
    finally:
        cleanup_case(root, process, worker)


def run_suite() -> dict:
    """Exercise parent-only and inherited lock fd in fresh private trees."""
    with tempfile.TemporaryDirectory(prefix=PREFIX, dir="/var/tmp") as base:
        root = Path(base)
        root.chmod(0o700)
        cases = [
            case(root / "parent-only", "parent_only"),
            case(root / "inherited", "inherited"),
        ]
        return {
            "cases": cases,
            "real_docker_supervisor": False,
            "signature_trust": "NOT_USED",
            "production_modified": False,
        }


def main() -> int:
    """Default dry-run, explicit consent and isolated guest guard."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-disposable", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    if not args.run_disposable:
        print("DRY_RUN: no processes launched")
        return 0
    if args.confirm != CONFIRM:
        print("BLOCKED: exact offline QA confirmation required")
        return 2
    try:
        guest_guard()
        result = run_suite()
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print("QA_BLOCKED", type(exc).__name__, str(exc)[:300])
        return 3
    print(json.dumps({"status": "PASS_INHERITED_B_LIFETIME_QA", **result}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
