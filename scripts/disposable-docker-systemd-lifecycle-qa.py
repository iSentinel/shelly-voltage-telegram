#!/usr/bin/env python3
"""Rehearse isolated Docker and transient systemd lifecycles, never Shelly."""

from __future__ import annotations

import argparse
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

CONFIRM = "DISPOSABLE_SVT_DOCKER_SYSTEMD_QA_ONLY"
PRODUCTION = "shelly-voltage-bot"
PREFIX = "svt-qa-lifecycle-"
TIMEOUT = 15
IMAGE_RE = re.compile(r"sha256:[a-f0-9]{64}\Z")


class QAError(Exception):
    """A controlled, fail-closed disposable lifecycle failure."""


def run(argv: list[str], *, allow_failure: bool = False) -> str:
    """Execute a fixed-argv command with timeout and bounded output."""
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise QAError("QA_COMMAND_UNAVAILABLE") from exc
    if len(result.stdout) > 4096 or len(result.stderr) > 4096:
        raise QAError("QA_COMMAND_OUTPUT_OVERSIZE")
    if result.returncode and not allow_failure:
        raise QAError("QA_COMMAND_FAILED")
    return result.stdout.strip()


def require_name(name: str) -> None:
    """Forbid all production or user-supplied container/unit identities."""
    if not re.fullmatch(r"svt-qa-lifecycle-[a-f0-9]{12}", name):
        raise QAError("QA_TARGET_NAME_UNSAFE")
    if name == PRODUCTION:
        raise QAError("PRODUCTION_TARGET_FORBIDDEN")


def docker(name: str, *args: str) -> str:
    """Operate only on an internally generated QA container name."""
    require_name(name)
    return run(["docker", *args, name])


def docker_state(name: str) -> tuple[str, str]:
    """Read the writer status and restart policy of the QA object."""
    values = docker(
        name,
        "inspect",
        "--format",
        "{{.State.Running}}|{{.HostConfig.RestartPolicy.Name}}",
    )
    parts = values.split("|")
    if len(parts) != 2 or parts[0] not in ("true", "false"):
        raise QAError("QA_DOCKER_INSPECT_INVALID")
    return parts[0], parts[1]


def production_identity() -> tuple[str, str, str, str]:
    """Read only the existing production container identity."""
    output = run(
        [
            "docker",
            "inspect",
            "--format",
            "{{.Id}}|{{.RestartCount}}|{{.State.Running}}|{{.Image}}",
            PRODUCTION,
        ]
    )
    parts = output.split("|")
    if len(parts) != 4 or not IMAGE_RE.fullmatch(parts[3]):
        raise QAError("PRODUCTION_INSPECT_UNAVAILABLE")
    return tuple(parts)


def container_creation_args(name: str, image_id: str) -> list[str]:
    """Build a fixed safe command that cannot launch the actual bot."""
    require_name(name)
    if not IMAGE_RE.fullmatch(image_id):
        raise QAError("QA_IMAGE_ID_INVALID")
    return [
        "docker",
        "create",
        "--name",
        name,
        "--label",
        "svt.qa.disposable=true",
        "--network",
        "none",
        "--read-only",
        "--no-healthcheck",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "32",
        "--memory",
        "64m",
        "--user",
        "65534:65534",
        "--restart",
        "unless-stopped",
        "--entrypoint",
        "/bin/sh",
        image_id,
        "-c",
        "while :; do sleep 1; done",
    ]


def create_container(name: str, image_id: str) -> None:
    """Create a non-root sleeper with no volumes, networks or ports."""
    run(container_creation_args(name, image_id))
    setting = docker(
        name,
        "inspect",
        "--format",
        "{{.HostConfig.NetworkMode}}|{{.HostConfig.ReadonlyRootfs}}"
        "|{{len .Mounts}}",
    )
    if setting != "none|true|0":
        raise QAError("QA_CONTAINER_ISOLATION_FAILED")


def require_stopped(name: str) -> None:
    """Fail closed if the disposable writer restarted unexpectedly."""
    if docker_state(name) != ("false", "no"):
        raise QAError("QA_WRITER_RESTARTED_OR_NOT_FENCED")


def run_docker_lifecycle(name: str, root: Path, image: str) -> None:
    """Exercise a real policy-neutralized stop/restart on QA only."""
    create_container(name, image)
    docker(name, "start")
    if docker_state(name) != ("true", "unless-stopped"):
        raise QAError("QA_WRITER_DID_NOT_START")
    docker(name, "update", "--restart=no")
    docker(name, "stop", "-t", "3")
    require_stopped(name)
    docker(name, "start")
    try:
        require_stopped(name)
    except QAError:
        print("PASS unexpected QA writer restart rejected")
    else:
        raise QAError("QA_RESTART_INJECTION_UNDETECTED")
    docker(name, "stop", "-t", "3")
    require_stopped(name)
    checkpoint = root / "qa-checkpoint.txt"
    with checkpoint.open("x", encoding="ascii") as output:
        output.write("DISPOSABLE_QA_ONLY\n")
        output.flush()
        os.fsync(output.fileno())
    require_stopped(name)
    docker(name, "start")
    if docker_state(name) != ("true", "no"):
        raise QAError("QA_CONTROLLED_START_UNVERIFIED")
    docker(name, "update", "--restart=unless-stopped")
    if docker_state(name) != ("true", "unless-stopped"):
        raise QAError("QA_RESTART_POLICY_NOT_RESTORED")
    print("PASS disposable Docker fenced stop/resume and restart injection")


def systemd_state(unit: str) -> str:
    """Inspect only the fixed generated transient unit."""
    if not re.fullmatch(r"svt-qa-lifecycle-[a-f0-9]{12}\.service", unit):
        raise QAError("QA_UNIT_NAME_UNSAFE")
    result = run(
        ["systemctl", "show", unit, "-p", "ActiveState", "--value"],
        allow_failure=True,
    )
    return result or "gone"


def run_systemd_lifecycle(unit: str) -> None:
    """Start and stop only one isolated, automatically collected unit."""
    if not unit.endswith(".service"):
        raise QAError("QA_UNIT_NAME_UNSAFE")
    require_name(unit.removesuffix(".service"))
    run(
        [
            "systemd-run",
            "--collect",
            "--unit",
            unit,
            "--property",
            "Type=simple",
            "--property",
            "Restart=on-failure",
            "--property",
            "NoNewPrivileges=yes",
            "--property",
            "PrivateTmp=yes",
            "--property",
            "MemoryMax=32M",
            "/bin/sleep",
            "60",
        ]
    )
    for _ in range(10):
        if systemd_state(unit) == "active":
            break
        time.sleep(0.15)
    else:
        raise QAError("QA_SYSTEMD_DID_NOT_START")
    run(["systemctl", "stop", unit])
    if systemd_state(unit) not in ("inactive", "gone"):
        raise QAError("QA_SYSTEMD_DID_NOT_STOP")
    print("PASS transient systemd unit start/stop")


def cleanup(name: str, unit: str) -> None:
    """Remove only own exact QA objects, even after partial failure."""
    require_name(name)
    require_name(unit.removesuffix(".service"))
    run(["systemctl", "stop", unit], allow_failure=True)
    run(["systemctl", "reset-failed", unit], allow_failure=True)
    run(["docker", "rm", "-f", name], allow_failure=True)


def prerequisites() -> None:
    """Require preinstalled local binaries and an exclusive root user."""
    if os.geteuid() != 0:
        raise QAError("QA_ROOT_REQUIRED")
    if any(
        shutil.which(x) is None for x in ("docker", "systemd-run", "systemctl")
    ):
        raise QAError("QA_REQUIRED_COMMAND_MISSING")


def execute() -> None:
    """Run two isolated lifecycles and verify production's identity."""
    prerequisites()
    original = production_identity()
    if original[2] != "true":
        raise QAError("PRODUCTION_NOT_RUNNING")
    token = secrets.token_hex(6)
    name = PREFIX + token
    unit = name + ".service"
    error = None
    with tempfile.TemporaryDirectory(
        prefix="svt-lifecycle-qa-", dir="/var/tmp"
    ) as directory:
        try:
            run_docker_lifecycle(name, Path(directory), original[3])
            run_systemd_lifecycle(unit)
        except QAError as exc:
            error = exc
        finally:
            cleanup(name, unit)
    if production_identity() != original:
        raise QAError("PRODUCTION_CONTAINER_IDENTITY_CHANGED")
    if error is not None:
        raise error
    print("PASS production container unchanged; QA objects cleaned")


def main() -> int:
    """Fail closed without explicit confirmation for the disposable run."""
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--run-disposable", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    if not args.run_disposable:
        print("DRY-RUN PASS: no commands executed, no state changed")
        print("Use --run-disposable --confirm", CONFIRM)
        return 0
    if args.confirm != CONFIRM:
        print("FAIL: explicit disposable-only confirmation required")
        return 2
    try:
        execute()
    except QAError as exc:
        print(f"FAIL disposable lifecycle: {exc}", file=sys.stderr)
        return 3
    print("PASS Docker/systemd disposable lifecycle; production untouched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
