#!/usr/bin/env python3
"""Real systemd B/A start/reload lock proof on networkless QA LXC114."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import secrets
import socket
import subprocess
import tempfile
from pathlib import Path

CONFIRM = "ISOLATED_SYSTEMD_BA_START_RELOAD_ONLY"
QA_WRITER = "svt-qa-signed-writer-114"
QA_UNITS = ("svt-qa-compose.service", "svt-qa-guarded-watchdog.timer")
UNIT_ROOT = Path("/run/systemd/system")

RUNNER = (
    "import fcntl,os,sys\n"
    "from pathlib import Path\n"
    "root=Path(sys.argv[2]);action=sys.argv[1]\n"
    "fd=os.open(root/'maintenance.lock',os.O_RDONLY|os.O_CLOEXEC)\n"
    "try: fcntl.flock(fd,fcntl.LOCK_SH|fcntl.LOCK_NB)\n"
    "except BlockingIOError:\n"
    " print('QA_B_LOCK_BUSY',action);sys.exit(3)\n"
    "with (root/'events').open('a') as out:out.write(action+'\\n')\n"
    "print('QA_GUARDED_'+action.upper())\n"
)


def command(argv: list[str], timeout: int = 12) -> subprocess.CompletedProcess:
    """Call only fixed systemd or Docker inspection tools with a deadline."""
    return subprocess.run(
        argv, capture_output=True, text=True, check=False, timeout=timeout
    )


def check_guest() -> None:
    """Reject any host besides networkless, stopped-writer QA LXC114."""
    if os.geteuid() != 0 or os.uname().nodename != "shelly-docker-reboot-qa":
        raise RuntimeError("DISPOSABLE_LXC114_ONLY")
    if {name for _, name in socket.if_nameindex()} != {"lo"}:
        raise RuntimeError("QA_EXTERNAL_NETWORK_PRESENT")
    for unit in QA_UNITS:
        if (
            command(["systemctl", "is-enabled", unit]).stdout.strip()
            != "disabled"
        ):
            raise RuntimeError("QA_ORIGINAL_OWNER_ENABLED")
    writer = command(
        [
            "docker",
            "inspect",
            "--format",
            "{{.State.Running}}|{{.HostConfig.RestartPolicy.Name}}"
            "|{{.HostConfig.NetworkMode}}",
            QA_WRITER,
        ]
    )
    if writer.returncode or writer.stdout.strip() != "false|no|none":
        raise RuntimeError("QA_ORIGINAL_WRITER_NOT_SAFE")


def systemctl(*args: str) -> subprocess.CompletedProcess:
    """Issue bounded systemctl actions only to the disposable unit."""
    return command(["systemctl", *args])


def prepare_unit(root: Path, unit: str) -> Path:
    """Create one private synthetic service unit and its lockfiles."""
    a = root / "updater.lock"
    b = root / "maintenance.lock"
    for path in (a, b, root / "authorize"):
        path.touch(mode=0o600, exist_ok=False)
    runner = root / "synthetic-runner.py"
    runner.write_text(RUNNER)
    runner.chmod(0o700)
    path = UNIT_ROOT / unit
    path.write_text(
        "[Unit]\n"
        "Description=QA-only B/A lock start-reload acceptance\n"
        f"ConditionPathExists={root}/authorize\n"
        "[Service]\nType=oneshot\nRemainAfterExit=yes\n"
        "NoNewPrivileges=yes\nPrivateNetwork=yes\n"
        "ProtectSystem=strict\n"
        f"ReadWritePaths={root}\n"
        f"ExecStartPre=/usr/bin/flock -w 2 {a} /usr/bin/true\n"
        f"ExecStart=/usr/bin/python3 {runner} start {root}\n"
        f"ExecReload=/usr/bin/python3 {runner} reload {root}\n"
        "ExecStop=/usr/bin/true\nTimeoutStartSec=7\n"
    )
    path.chmod(0o644)
    return path


def assert_action(unit: str, action: str, expected: bool) -> None:
    """Reject an unexpected real systemd action result."""
    result = systemctl(action, unit)
    if (result.returncode == 0) != expected:
        raise RuntimeError(f"QA_{action.upper()}_RESULT_MISMATCH")


def read_events(root: Path) -> list[str]:
    """Read only the disposable test event record."""
    path = root / "events"
    return path.read_text().splitlines() if path.exists() else []


def check_start(root: Path, unit: str, result: dict) -> None:
    """Test B EX failure and B SH success through actual ExecStartPre."""
    b = root / "maintenance.lock"
    with b.open("rb") as owner:
        fcntl.flock(owner, fcntl.LOCK_EX)
        assert_action(unit, "start", False)
        if read_events(root):
            raise RuntimeError("B_EX_ALLOWED_START")
    assert_action(unit, "reset-failed", True)
    with b.open("rb") as owner:
        fcntl.flock(owner, fcntl.LOCK_SH)
        assert_action(unit, "start", True)
    if read_events(root) != ["start"]:
        raise RuntimeError("B_SH_PRESTART_FAILED")
    result["B_EX_START_BLOCKED"] = True
    result["B_SH_WITH_DISTINCT_A_PRESTART_PASS"] = True


def check_reload(root: Path, unit: str, result: dict) -> None:
    """Test the actual ExecReload and enforce B shared lock eligibility."""
    b = root / "maintenance.lock"
    with b.open("rb") as owner:
        fcntl.flock(owner, fcntl.LOCK_EX)
        assert_action(unit, "reload", False)
        if read_events(root) != ["start"]:
            raise RuntimeError("B_EX_ALLOWED_RELOAD")
    assert_action(unit, "reload", True)
    if read_events(root) != ["start", "reload"]:
        raise RuntimeError("B_FREE_RELOAD_FAILED")
    result["B_EX_RELOAD_BLOCKED"] = True
    result["B_FREE_RELOAD_PASS"] = True


def check_prestart(root: Path, unit: str, result: dict) -> None:
    """Prove A mutex wait is bounded under shared maintenance B."""
    a = root / "updater.lock"
    b = root / "maintenance.lock"
    with b.open("rb") as owner, a.open("rb") as updater:
        fcntl.flock(owner, fcntl.LOCK_SH)
        fcntl.flock(updater, fcntl.LOCK_EX)
        assert_action(unit, "restart", False)
        if read_events(root) != ["start", "reload"]:
            raise RuntimeError("A_EX_ALLOWED_PRESTART")
    assert_action(unit, "reset-failed", True)
    assert_action(unit, "start", True)
    if read_events(root) != ["start", "reload", "start"]:
        raise RuntimeError("A_RELEASE_START_FAILED")
    result["A_EX_BOUNDED_PRESTART_BLOCKED"] = True
    result["A_RELEASE_RESTART_PASS"] = True


def run_disposable() -> dict:
    """Execute only synthetic systemd unit actions, clean unconditionally."""
    check_guest()
    with tempfile.TemporaryDirectory(
        prefix="svt-qa-ba-systemd-", dir="/var/tmp"
    ) as folder:
        root = Path(folder)
        root.chmod(0o700)
        unit = "svt-qa-ba-owner-" + secrets.token_hex(5) + ".service"
        path = UNIT_ROOT / unit
        outcome: dict = {}
        try:
            prepare_unit(root, unit)
            if systemctl("daemon-reload").returncode != 0:
                raise RuntimeError("QA_DAEMON_RELOAD_FAILED")
            verify = command(["systemd-analyze", "verify", str(path)])
            if verify.returncode:
                raise RuntimeError("QA_SYSTEMD_UNIT_VERIFY_FAILED")
            check_start(root, unit, outcome)
            check_reload(root, unit, outcome)
            check_prestart(root, unit, outcome)
        finally:
            systemctl("stop", unit)
            systemctl("reset-failed", unit)
            path.unlink(missing_ok=True)
            systemctl("daemon-reload")
        if path.exists():
            raise RuntimeError("QA_SYSTEMD_UNIT_NOT_CLEANED")
    return {
        "status": "PASS_DISPOSABLE_SYSTEMD_BA_OWNER",
        "checks": outcome,
        "production_modified": False,
        "real_docker_writer_started": False,
        "real_updater_invoked": False,
        "telegram_contacted": False,
        "owner_maintenance_authorized": False,
    }


def main() -> int:
    """Require exact QA consent for real disposable systemd operations."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--run-disposable", action="store_true")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--jsonl", action="store_true")
    args = parser.parse_args()
    if args.dry_run or not args.run_disposable:
        print("DRY-RUN PASS: no service, lock or Docker accessed")
        return 0
    if args.confirm != CONFIRM:
        print("FAIL: exact disposable systemd confirmation required")
        return 2
    try:
        result = run_disposable()
    except (
        AssertionError,
        OSError,
        RuntimeError,
        subprocess.TimeoutExpired,
    ) as exc:
        print("QA_SYSTEMD_BLOCKED", type(exc).__name__, str(exc))
        return 3
    print(json.dumps(result) if args.jsonl else result["status"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
