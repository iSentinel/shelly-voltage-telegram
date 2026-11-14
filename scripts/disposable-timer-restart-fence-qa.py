#!/usr/bin/env python3
"""Prove a disposable systemd timer can restart an unfenced QA writer."""

from __future__ import annotations

import argparse
import re
import secrets
import shutil
import subprocess
import sys
import time

CONFIRM = "DISPOSABLE_SYSTEMD_TIMER_FENCE_QA_ONLY"
NAME_RE = re.compile(r"svt-qa-timer-[a-f0-9]{12}\Z")
IMAGE_RE = re.compile(r"sha256:[a-f0-9]{64}\Z")
PRODUCTION = "shelly-voltage-bot"


class Blocked(Exception):
    """A disposable-only control cannot safely continue."""


def run(args: list[str], *, allow_failure: bool = False) -> str:
    """Run exact argv operations with bounded output and timeout."""
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Blocked("QA_SYSTEM_COMMAND_UNAVAILABLE") from exc
    if len(result.stdout) > 4096 or len(result.stderr) > 4096:
        raise Blocked("QA_COMMAND_OUTPUT_OVERSIZED")
    if result.returncode and not allow_failure:
        raise Blocked("QA_SYSTEM_COMMAND_FAILED")
    return result.stdout.strip()


def qa_name(name: str) -> None:
    """Refuse any production or caller-chosen Docker/systemd identity."""
    if not NAME_RE.fullmatch(name) or name == PRODUCTION:
        raise Blocked("QA_NAME_UNSAFE")


def timer_unit(name: str, phase: str) -> str:
    """Build a generated one-shot timer unit name for an allowed phase."""
    qa_name(name)
    if phase not in ("unfenced", "fenced"):
        raise Blocked("QA_TIMER_PHASE_UNSAFE")
    return name + "-" + phase


def production_identity() -> tuple[str, str, str, str]:
    """Read live bot identity only, without changing the container."""
    output = run(
        [
            "docker",
            "inspect",
            "--format",
            "{{.Id}}|{{.RestartCount}}|{{.State.Running}}|{{.Image}}",
            PRODUCTION,
        ]
    )
    values = output.split("|")
    if len(values) != 4 or not IMAGE_RE.fullmatch(values[3]):
        raise Blocked("QA_PRODUCTION_METADATA_UNAVAILABLE")
    return tuple(values)


def creation_args(name: str, image: str) -> list[str]:
    """Build a networkless sleeper with no mounts, credentials or ports."""
    qa_name(name)
    if not IMAGE_RE.fullmatch(image):
        raise Blocked("QA_IMAGE_ID_INVALID")
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
        "--memory",
        "64m",
        "--pids-limit",
        "32",
        "--user",
        "65534:65534",
        "--restart=no",
        "--entrypoint",
        "/bin/sh",
        image,
        "-c",
        "while :; do sleep 1; done",
    ]


def docker(name: str, action: str, *options: str) -> str:
    """Restrict Docker controls to the generated test container only."""
    qa_name(name)
    if action not in ("start", "stop", "inspect"):
        raise Blocked("QA_DOCKER_ACTION_UNSAFE")
    return run(["docker", action, *options, name])


def running(name: str) -> bool:
    """Return the live QA container state without touching production."""
    state = docker(name, "inspect", "--format", "{{.State.Running}}")
    if state not in ("true", "false"):
        raise Blocked("QA_DOCKER_STATE_UNTRUSTED")
    return state == "true"


def create_sleeper(name: str, image: str) -> None:
    """Create and verify one unconnected, resource-limited test sleeper."""
    run(creation_args(name, image))
    isolation = docker(
        name,
        "inspect",
        "--format",
        "{{.HostConfig.NetworkMode}}|{{len .Mounts}}"
        "|{{.HostConfig.ReadonlyRootfs}}"
        "|{{.HostConfig.RestartPolicy.Name}}",
    )
    if isolation != "none|0|true|no":
        raise Blocked("QA_ISOLATION_INCOMPLETE")
    docker(name, "start")
    docker(name, "stop", "-t", "3")
    if running(name):
        raise Blocked("QA_SLEEpER_NOT_STOPPED")


def start_timer(name: str, phase: str) -> str:
    """Arm a precision-bounded one-shot timer for the QA name alone."""
    base = timer_unit(name, phase)
    delay = "3s" if phase == "unfenced" else "4s"
    run(
        [
            "systemd-run",
            "--collect",
            "--unit",
            base,
            f"--on-active={delay}",
            "--timer-property=AccuracySec=1ms",
            "--timer-property=RandomizedDelaySec=0",
            "--property=Type=oneshot",
            "/usr/bin/docker",
            "start",
            name,
        ]
    )
    return base


def stop_timer(name: str, phase: str) -> None:
    """Stop both generated unit objects without addressing live units."""
    base = timer_unit(name, phase)
    run(
        [
            "systemctl",
            "stop",
            base + ".timer",
            base + ".service",
        ],
        allow_failure=True,
    )
    run(
        [
            "systemctl",
            "reset-failed",
            base + ".service",
        ],
        allow_failure=True,
    )


def await_running(name: str) -> None:
    """Check an unfenced timer really restarts the stopped QA writer."""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if running(name):
            return
        time.sleep(0.25)
    raise Blocked("QA_UNFENCED_TIMER_DID_NOT_RESTART")


def verify_fenced_timer(name: str) -> None:
    """Prove stopping the second timer prevents QA writer restart."""
    start_timer(name, "fenced")
    stop_timer(name, "fenced")
    for _ in range(22):
        if running(name):
            raise Blocked("QA_FENCED_TIMER_RESTARTED_WRITER")
        time.sleep(0.25)


def cleanup(name: str) -> None:
    """Always clean only generated Docker and systemd QA objects."""
    qa_name(name)
    for phase in ("unfenced", "fenced"):
        stop_timer(name, phase)
    run(["docker", "rm", "-f", name], allow_failure=True)


def verify_no_orphans(name: str) -> None:
    """Require no remaining exact QA container or active timer."""
    qa_name(name)
    output = run(
        [
            "docker",
            "ps",
            "-a",
            "--format",
            "{{.Names}}",
            "--filter",
            f"name=^{name}$",
        ]
    )
    if output:
        raise Blocked("QA_CONTAINER_NOT_CLEANED")
    for phase in ("unfenced", "fenced"):
        base = timer_unit(name, phase)
        result = run(
            [
                "systemctl",
                "show",
                base + ".timer",
                "-p",
                "ActiveState",
                "--value",
            ],
            allow_failure=True,
        )
        if result == "active":
            raise Blocked("QA_TIMER_NOT_CLEANED")


def prerequisites() -> None:
    """Require a root-owned test-capable host and basic binaries."""
    import os

    if os.geteuid() != 0:
        raise Blocked("QA_ROOT_REQUIRED")
    for binary in ("docker", "systemctl", "systemd-run"):
        if shutil.which(binary) is None:
            raise Blocked("QA_SYSTEM_BINARY_MISSING")


def execute() -> None:
    """Run isolated timer hazard and fenced-timer control experiments."""
    prerequisites()
    baseline = production_identity()
    if baseline[2] != "true":
        raise Blocked("QA_PRODUCTION_NOT_RUNNING")
    name = "svt-qa-timer-" + secrets.token_hex(6)
    error = None
    try:
        create_sleeper(name, baseline[3])
        start_timer(name, "unfenced")
        await_running(name)
        print("PASS unfenced QA timer restarted stopped QA writer")
        stop_timer(name, "unfenced")
        docker(name, "stop", "-t", "3")
        if running(name):
            raise Blocked("QA_WRITER_NOT_FENCED")
        verify_fenced_timer(name)
        print("PASS fenced QA timer did not restart stopped QA writer")
    except Blocked as exc:
        error = exc
    finally:
        cleanup(name)
    verify_no_orphans(name)
    if production_identity() != baseline:
        raise Blocked("QA_PRODUCTION_IDENTITY_CHANGED")
    if error is not None:
        raise error
    print("PASS disposable restart-owner drill; production unchanged")


def main() -> int:
    """Expose explicit QA opt-in and stable success/failure status."""
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--dry-run", action="store_true")
    modes.add_argument("--run-disposable", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    if not args.run_disposable:
        print("DRY-RUN PASS: no Docker or systemd commands executed")
        return 0
    if args.confirm != CONFIRM:
        print("FAIL: exact disposable-only confirmation required")
        return 2
    try:
        execute()
    except Blocked as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
