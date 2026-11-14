#!/usr/bin/env python3
"""Prove a disposable systemd ExecStartPre can restart a stopped QA writer."""

from __future__ import annotations

import argparse
import os
import re
import secrets
import shutil
import subprocess
import sys
import time

CONFIRM = "DISPOSABLE_BOOT_PRESTART_HOOK_QA_ONLY"
NAME = re.compile(r"svt-qa-boot-[a-f0-9]{12}\Z")
IMAGE = re.compile(r"sha256:[a-f0-9]{64}\Z")
PRODUCTION = "shelly-voltage-bot"


class Blocked(Exception):
    """A disposable-only pre-start hook safety condition failed."""


def run(arguments: list[str], *, allow_failure: bool = False) -> str:
    """Run only argv operations and fail closed on command errors."""
    try:
        result = subprocess.run(
            arguments,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Blocked("QA_SYSTEM_COMMAND_UNAVAILABLE") from exc
    if len(result.stdout) > 4096 or len(result.stderr) > 4096:
        raise Blocked("QA_UNEXPECTED_COMMAND_OUTPUT")
    if result.returncode and not allow_failure:
        raise Blocked("QA_SYSTEM_COMMAND_FAILED")
    return result.stdout.strip()


def safe_name(name: str) -> None:
    """Reject all production names and arbitrary systemd targets."""
    if not NAME.fullmatch(name) or name == PRODUCTION:
        raise Blocked("QA_NAME_UNSAFE")


def unit(name: str, phase: str) -> str:
    """Only two generated transient services are permitted."""
    safe_name(name)
    if phase not in ("hook", "control"):
        raise Blocked("QA_UNIT_PHASE_UNSAFE")
    return name + "-" + phase + ".service"


def production_identity() -> tuple[str, ...]:
    """Read the live bot container identity without modifying it."""
    parts = run(
        [
            "docker",
            "inspect",
            "--format",
            "{{.Id}}|{{.RestartCount}}|{{.State.Running}}|{{.Image}}",
            PRODUCTION,
        ]
    ).split("|")
    if len(parts) != 4 or not IMAGE.fullmatch(parts[3]):
        raise Blocked("QA_PRODUCTION_INSPECT_UNAVAILABLE")
    return tuple(parts)


def production_owner_states() -> tuple[str, str]:
    """Inspect the real Compose and updater timer state read-only."""
    return tuple(
        run(
            [
                "systemctl",
                "show",
                name,
                "-p",
                "ActiveState",
                "-p",
                "UnitFileState",
                "--no-pager",
            ]
        )
        for name in (
            "shelly-voltage-compose.service",
            "shelly-voltage-update-check.timer",
        )
    )


def docker(name: str, action: str, *args: str) -> str:
    """Operate only on a generated test Docker name."""
    safe_name(name)
    if action not in ("inspect", "start", "stop"):
        raise Blocked("QA_DOCKER_OPERATION_UNSAFE")
    return run(["docker", action, *args, name])


def state(name: str) -> tuple[str, str, str, str]:
    """Check running status, restart policy, network and mount isolation."""
    parts = docker(
        name,
        "inspect",
        "--format",
        "{{.State.Running}}|{{.HostConfig.RestartPolicy.Name}}"
        "|{{.HostConfig.NetworkMode}}|{{len .Mounts}}",
    ).split("|")
    if len(parts) != 4 or parts[0] not in ("true", "false"):
        raise Blocked("QA_DOCKER_STATE_INVALID")
    return tuple(parts)


def create_container(name: str, image: str) -> None:
    """Create a non-root networkless QA sleeper without volumes or ports."""
    safe_name(name)
    if not IMAGE.fullmatch(image):
        raise Blocked("QA_IMAGE_INVALID")
    run(
        [
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
            "--memory",
            "64m",
            "--pids-limit",
            "32",
            "--user",
            "65534:65534",
            "--restart=no",
            "--entrypoint=/bin/sh",
            image,
            "-c",
            "while :; do sleep 1; done",
        ]
    )
    if state(name) != ("false", "no", "none", "0"):
        raise Blocked("QA_CONTAINER_NOT_ISOLATED")


def start_prestart_hook(name: str) -> None:
    """Start a disposable service with Docker start in its ExecStartPre."""
    safe_name(name)
    run(
        [
            "systemd-run",
            "--collect",
            "--unit",
            unit(name, "hook").removesuffix(".service"),
            "--property=Type=oneshot",
            f"--property=ExecStartPre=/usr/bin/docker start {name}",
            "/usr/bin/true",
        ]
    )


def start_control_service(name: str) -> None:
    """Start a matching QA unit without any pre-start Docker operation."""
    safe_name(name)
    run(
        [
            "systemd-run",
            "--collect",
            "--unit",
            unit(name, "control").removesuffix(".service"),
            "--property=Type=oneshot",
            "/usr/bin/true",
        ]
    )


def await_running(name: str) -> None:
    """Require a real test writer start after invoking ExecStartPre."""
    deadline = time.monotonic() + 9
    while time.monotonic() < deadline:
        if state(name) == ("true", "no", "none", "0"):
            return
        time.sleep(0.2)
    raise Blocked("QA_PRESTART_HOOK_DID_NOT_START_WRITER")


def verify_stays_stopped(name: str) -> None:
    """Control unit without a hook must not start the test writer."""
    for _ in range(9):
        if state(name) != ("false", "no", "none", "0"):
            raise Blocked("QA_CONTROL_SERVICE_STARTED_WRITER")
        time.sleep(0.2)


def cleanup(name: str) -> None:
    """Stop only generated services and remove only the QA container."""
    safe_name(name)
    for phase in ("hook", "control"):
        target = unit(name, phase)
        run(["systemctl", "stop", target], allow_failure=True)
        run(["systemctl", "reset-failed", target], allow_failure=True)
    run(["docker", "rm", "-f", name], allow_failure=True)


def verify_cleanup(name: str) -> None:
    """Reject leftover test Docker objects or active transient units."""
    safe_name(name)
    if run(
        [
            "docker",
            "ps",
            "-aq",
            "--filter",
            f"name=^{name}$",
        ]
    ):
        raise Blocked("QA_DOCKER_OBJECT_NOT_CLEANED")
    for phase in ("hook", "control"):
        active = run(
            [
                "systemctl",
                "show",
                unit(name, phase),
                "-p",
                "ActiveState",
                "--value",
            ],
            allow_failure=True,
        )
        if active == "active":
            raise Blocked("QA_TRANSIENT_SERVICE_STILL_ACTIVE")


def prerequisites() -> None:
    """Require root and locally installed Docker/systemd facilities."""
    if os.geteuid() != 0:
        raise Blocked("QA_ROOT_REQUIRED")
    for executable in ("docker", "systemctl", "systemd-run"):
        if shutil.which(executable) is None:
            raise Blocked("QA_DEPENDENCY_MISSING")


def exercise(name: str) -> None:
    """Prove hook restart and no-hook control on one stopped QA container."""
    start_prestart_hook(name)
    await_running(name)
    print("PASS transient QA ExecStartPre independently started writer")
    docker(name, "stop", "-t", "3")
    if state(name) != ("false", "no", "none", "0"):
        raise Blocked("QA_WRITER_NOT_STOPPED_AFTER_HOOK")
    start_control_service(name)
    verify_stays_stopped(name)
    print("PASS QA service without ExecStartPre did not start writer")


def execute() -> None:
    """Run disposable pre-start simulation and verify production unchanged."""
    prerequisites()
    before = production_identity()
    owners = production_owner_states()
    if before[2] != "true":
        raise Blocked("QA_PRODUCTION_NOT_RUNNING")
    name = "svt-qa-boot-" + secrets.token_hex(6)
    failed: Blocked | None = None
    try:
        create_container(name, before[3])
        exercise(name)
    except Blocked as exc:
        failed = exc
    finally:
        cleanup(name)
        verify_cleanup(name)
    if production_identity() != before:
        raise Blocked("PRODUCTION_DOCKER_IDENTITY_CHANGED")
    if production_owner_states() != owners:
        raise Blocked("PRODUCTION_SYSTEMD_OWNER_CHANGED")
    if failed is not None:
        raise failed
    print("PASS disposable pre-start hook drill, production unchanged")


def main() -> int:
    """Default to dry-run and require explicit QA-only confirmation."""
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument("--dry-run", action="store_true")
    choice.add_argument("--run-disposable", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    if not args.run_disposable:
        print("DRY-RUN PASS: no Docker/systemd commands executed")
        return 0
    if args.confirm != CONFIRM:
        print("FAIL: exact disposable QA confirmation required")
        return 2
    try:
        execute()
    except Blocked as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
