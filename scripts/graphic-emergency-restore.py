#!/usr/bin/env python3
"""Independent timed restoration for an owner-approved graphic cutover."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import stat
import subprocess
import tempfile
from contextlib import suppress
from pathlib import Path

UNIT = "shelly-voltage-compose.service"
CONTAINER = "shelly-voltage-bot"
TIMER = "shelly-graphic-emergency-restore.timer"
STABLE = Path("/usr/local/sbin/shelly-graphic-emergency-restore")
SCRIPTS = Path(__file__).resolve().parent
BOOT_UNIT = "shelly-graphic-emergency-boot.service"
BOOT_FILE = Path("/etc/systemd/system") / BOOT_UNIT
STATE_DIR = Path("/var/lib/shelly-graphic-emergency")
ARMED_MARKER = STATE_DIR / "armed"


class RestoreBlocked(Exception):
    """Emit stable failure labels without sensitive runtime values."""


def command(args: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    """Execute one bounded local command and suppress sensitive output."""

    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        raise RestoreBlocked("SERVICE_COMMAND_UNAVAILABLE") from None
    if result.returncode:
        raise RestoreBlocked("SERVICE_COMMAND_FAILED")
    return result


def state(args: list[str]) -> str:
    """Read one exact systemd or Docker value without changing it."""

    return command(args, timeout=7).stdout.strip()


def production_running() -> bool:
    """Require an active service, healthy container and boot protection."""

    try:
        return (
            state(["systemctl", "is-active", UNIT]) == "active"
            and state(["systemctl", "is-enabled", UNIT]) == "enabled"
            and state(
                [
                    "docker",
                    "inspect",
                    "--format",
                    "{{.State.Running}}",
                    CONTAINER,
                ]
            )
            == "true"
            and state(
                [
                    "docker",
                    "inspect",
                    "--format",
                    "{{.State.Health.Status}}",
                    CONTAINER,
                ]
            )
            == "healthy"
            and state(
                [
                    "docker",
                    "inspect",
                    "--format",
                    "{{.HostConfig.RestartPolicy.Name}}",
                    CONTAINER,
                ]
            )
            == "unless-stopped"
        )
    except RestoreBlocked:
        return False


def approval(path: Path) -> bool:
    """Reuse the pinned owner-approval validator before arming."""

    script = SCRIPTS / "graphic-rollout-readiness.py"
    spec = importlib.util.spec_from_file_location(
        "watchdog_owner_gate", script
    )
    if not spec or not spec.loader:
        return False
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    return guard.owner_approval_verified(path)


def install_stable_copy() -> None:
    """Pin this exact script outside the candidate source checkout."""

    source = Path(__file__).resolve()
    parent = STABLE.parent
    if source.is_symlink() or not source.is_file():
        raise RestoreBlocked("SOURCE_UNSAFE")
    if parent.is_symlink() or not parent.is_dir():
        raise RestoreBlocked("STABLE_DIRECTORY_UNSAFE")
    directory = parent.stat()
    if directory.st_uid != 0 or directory.st_mode & 0o022:
        raise RestoreBlocked("STABLE_DIRECTORY_UNSAFE")
    if STABLE.exists() or STABLE.is_symlink():
        data = STABLE.lstat()
        if (
            not stat.S_ISREG(data.st_mode)
            or data.st_uid != 0
            or stat.S_IMODE(data.st_mode) != 0o700
            or STABLE.read_bytes() != source.read_bytes()
        ):
            raise RestoreBlocked("STABLE_COPY_CONFLICT")
        return
    with tempfile.NamedTemporaryFile(
        dir=STABLE.parent, prefix=".graphic-restore-", delete=False
    ) as f:
        staged = Path(f.name)
        f.write(source.read_bytes())
        f.flush()
        os.fsync(f.fileno())
    try:
        staged.chmod(0o700)
        os.replace(staged, STABLE)
    finally:
        staged.unlink(missing_ok=True)


def boot_service_content() -> str:
    """Provide a boot-enabled rescue unit that never reads bot credentials."""

    return (
        "[Unit]\\n"
        "Description=Shelly graphic cutover boot emergency restoration\\n"
        "ConditionPathExists=/var/lib/shelly-graphic-emergency/armed\\n"
        "Wants=docker.service\\nAfter=docker.service\\n"
        "StartLimitIntervalSec=0\\n"
        "[Service]\\nType=oneshot\\n"
        "ExecStart=/usr/bin/python3 "
        "/usr/local/sbin/shelly-graphic-emergency-restore "
        "--restore --execute\\n"
        "Restart=on-failure\\nRestartSec=60s\\n"
        "TimeoutStartSec=120s\\n"
        "[Install]\\nWantedBy=multi-user.target\\n"
    ).replace("\\n", "\n")


def install_boot_service() -> None:
    """Pin a separate enabled rescue service for LXC restarts."""

    parent = BOOT_FILE.parent
    meta = parent.stat()
    if parent.is_symlink() or meta.st_uid != 0 or meta.st_mode & 0o022:
        raise RestoreBlocked("BOOT_UNIT_DIRECTORY_UNSAFE")
    content = boot_service_content().encode("utf-8")
    if BOOT_FILE.exists() or BOOT_FILE.is_symlink():
        meta = BOOT_FILE.lstat()
        if (
            not stat.S_ISREG(meta.st_mode)
            or meta.st_uid != 0
            or stat.S_IMODE(meta.st_mode) != 0o644
            or BOOT_FILE.read_bytes() != content
        ):
            raise RestoreBlocked("BOOT_UNIT_CONFLICT")
        return
    fd = os.open(BOOT_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    with os.fdopen(fd, "wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def write_arm_marker() -> None:
    """Create one root-only reboot-persistent cutover marker."""

    if not STATE_DIR.exists():
        STATE_DIR.mkdir(mode=0o700)
    info = STATE_DIR.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0:
        raise RestoreBlocked("MARKER_DIRECTORY_UNSAFE")
    if stat.S_IMODE(info.st_mode) != 0o700 or ARMED_MARKER.exists():
        raise RestoreBlocked("MARKER_CONFLICT")
    fd = os.open(ARMED_MARKER, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write("owner-approved-cutover\\n".replace("\\n", "\n"))
        stream.flush()
        os.fsync(stream.fileno())


def boot_rescue_armed() -> bool:
    """Confirm the reboot-protection marker and enabled systemd unit."""

    try:
        info = ARMED_MARKER.lstat()
        return (
            stat.S_ISREG(info.st_mode)
            and info.st_uid == 0
            and stat.S_IMODE(info.st_mode) == 0o600
            and state(["systemctl", "is-enabled", BOOT_UNIT]) == "enabled"
        )
    except (OSError, RestoreBlocked):
        return False


def arm(minutes: int, owner: Path) -> dict:
    """Schedule independent restoration before any writer stop."""

    if os.geteuid() != 0 or not 5 <= minutes <= 30:
        raise RestoreBlocked("ARM_UNSAFE")
    if not approval(owner):
        raise RestoreBlocked("OWNER_APPROVAL_INVALID")
    if not production_running():
        raise RestoreBlocked("PRODUCTION_BASELINE_NOT_HEALTHY")
    if timer_armed():
        raise RestoreBlocked("WATCHDOG_ALREADY_ACTIVE")
    install_stable_copy()
    install_boot_service()
    write_arm_marker()
    command(["systemctl", "daemon-reload"])
    command(["systemctl", "enable", BOOT_UNIT])
    if not boot_rescue_armed():
        raise RestoreBlocked("BOOT_RESCUE_NOT_ARMED")
    command(
        [
            "systemd-run",
            "--unit=shelly-graphic-emergency-restore",
            f"--on-active={minutes}min",
            "--on-unit-active=1min",
            "--timer-property=AccuracySec=1s",
            "/usr/bin/python3",
            str(STABLE),
            "--restore",
            "--execute",
        ]
    )
    if not timer_armed() or not boot_rescue_armed():
        raise RestoreBlocked("WATCHDOG_NOT_ARMED")
    return {
        "status": "ARMED",
        "boot_rescue_armed": True,
        "minutes": minutes,
        "service_modified": False,
        "writer_stopped": False,
    }


def timer_armed() -> bool:
    """Require a separate active systemd timer to own the countdown."""

    try:
        return state(["systemctl", "is-active", TIMER]) == "active"
    except RestoreBlocked:
        return False


def restore() -> dict:
    """Restore startup independently, including on interrupted sessions."""

    if os.geteuid() != 0:
        raise RestoreBlocked("ROOT_REQUIRED")
    if production_running():
        return {"status": "ALREADY_RESTORED"}
    command(["systemctl", "unmask", "--runtime", UNIT])
    # Compose may recreate a missing container after a failed Docker update.
    with suppress(RestoreBlocked):
        command(["docker", "update", "--restart=unless-stopped", CONTAINER])
    command(["systemctl", "enable", UNIT])
    command(["systemctl", "restart", UNIT], timeout=90)
    if not production_running():
        raise RestoreBlocked("RESTORE_NOT_VERIFIED")
    return {"status": "RESTORED"}


def disarm() -> dict:
    """Cancel a watchdog only after both startup paths are healthy."""

    if os.geteuid() != 0 or not production_running():
        raise RestoreBlocked("PRODUCTION_NOT_RESTORED")
    if timer_armed():
        command(["systemctl", "stop", TIMER])
    if boot_rescue_armed():
        command(["systemctl", "disable", BOOT_UNIT])
        ARMED_MARKER.unlink()
    return {"status": "DISARMED", "production_healthy": True}


def main() -> int:
    """Require explicit action; default to a non-mutating state audit."""

    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--arm", action="store_true")
    cli.add_argument("--restore", action="store_true")
    cli.add_argument("--disarm", action="store_true")
    cli.add_argument("--execute", action="store_true")
    cli.add_argument("--minutes", type=int, default=15)
    cli.add_argument("--owner-approval-file", type=Path)
    args = cli.parse_args()
    try:
        chosen = sum((args.arm, args.restore, args.disarm))
        if chosen > 1 or (chosen and not args.execute):
            raise RestoreBlocked("EXPLICIT_EXECUTION_REQUIRED")
        if args.arm:
            if args.owner_approval_file is None:
                raise RestoreBlocked("OWNER_APPROVAL_REQUIRED")
            result = arm(args.minutes, args.owner_approval_file)
        elif args.restore:
            result = restore()
        elif args.disarm:
            result = disarm()
        else:
            result = {
                "status": "READ_ONLY",
                "timer_armed": timer_armed(),
                "boot_rescue_armed": boot_rescue_armed(),
                "production_running": production_running(),
            }
    except (RestoreBlocked, OSError, ValueError) as error:
        print(json.dumps({"status": "BLOCKED", "reason": str(error)}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
