#!/usr/bin/env python3
"""Observe Docker/Compose/systemd boot-owner conflicts without writes."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

CONTAINER = re.compile(r"(shelly-voltage-bot|svt-qa-signed-writer-[0-9]+)\Z")
SERVICE = re.compile(r"(shelly-voltage-compose|svt-qa-compose)\.service\Z")


class Blocked(Exception):
    """An observation was unavailable or an unsafe input was requested."""


def command(args: list[str]) -> str:
    """Run only a bounded, non-shell, read-only inspection."""
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Blocked("OBSERVATION_COMMAND_FAILED") from exc
    if result.returncode or len(result.stdout) > 65536:
        raise Blocked("OBSERVATION_UNAVAILABLE")
    return result.stdout.strip()


def ensure_file(path: Path) -> None:
    """Reject paths that are relative, linked or not regular files."""
    if (
        not path.is_absolute()
        or any(entry.is_symlink() for entry in (path, *path.parents))
        or not path.is_file()
    ):
        raise Blocked("COMPOSE_CONFIG_PATH_INVALID")


def docker_state(container: str) -> dict:
    """Observe exactly Docker running state and restart policy."""
    raw = command(
        [
            "docker",
            "inspect",
            "--format",
            "{{.HostConfig.RestartPolicy.Name}}|{{.State.Running}}",
            container,
        ]
    ).split("|")
    if len(raw) != 2 or raw[1] not in {"true", "false"}:
        raise Blocked("DOCKER_STATE_INVALID")
    return {"restart_policy": raw[0], "running": raw[1] == "true"}


def compose_state(file: Path, container: str) -> dict:
    """Inspect one rendered Compose service but never expose env values."""
    plan = json.loads(
        command(
            [
                "docker",
                "compose",
                "-f",
                str(file),
                "config",
                "--format",
                "json",
            ]
        )
    )
    services = plan.get("services")
    if not isinstance(services, dict) or len(services) != 1:
        raise Blocked("COMPOSE_REQUIRES_EXACTLY_ONE_SERVICE")
    service = next(iter(services.values()))
    if service.get("container_name") != container:
        raise Blocked("COMPOSE_CONTAINER_IDENTITY_MISMATCH")
    return {
        "restart_policy": service.get("restart", "no"),
        "network_mode": service.get("network_mode"),
        "ports_present": bool(service.get("ports")),
    }


def systemd_state(unit: str) -> dict:
    """Read boot preflight and start commands without executing them."""
    raw = command(
        [
            "systemctl",
            "show",
            unit,
            "-p",
            "ExecStartPre",
            "-p",
            "ExecStart",
            "-p",
            "ActiveState",
            "-p",
            "UnitFileState",
            "--no-pager",
        ]
    )
    props = {}
    for line in raw.splitlines():
        key, _, val = line.partition("=")
        props[key] = val
    if "ActiveState" not in props or "ExecStart" not in props:
        raise Blocked("SYSTEMD_STATE_INVALID")
    prestart = props.get("ExecStartPre", "")
    start = props.get("ExecStart", "")
    return {
        "active": props.get("ActiveState", ""),
        "enabled": props.get("UnitFileState") == "enabled",
        "prestart_present": any(
            marker in prestart for marker in ("--recover", "/prestart.py")
        ),
        "start_present": (
            "start-compose.sh" in start
            or ("compose" in start and "/docker" in start)
        ),
    }


def pending_state(path: Path) -> bool:
    """Check only the marker's existence, never read its contents."""
    if not path.is_absolute() or any(
        entry.is_symlink() for entry in (path, *path.parents)
    ):
        raise Blocked("PENDING_PATH_INVALID")
    return path.exists()


def classify(docker: dict, compose: dict, unit: dict, pending: bool) -> dict:
    """Report observed single-owner safety, never authorize boot."""
    reasons = []
    if pending and docker["running"]:
        reasons.append("PENDING_BUT_DOCKER_WRITER_RUNNING")
    if docker["restart_policy"] != "no":
        reasons.append("DOCKER_INDEPENDENT_RESTART_OWNER")
    if compose["restart_policy"] != "no":
        reasons.append("COMPOSE_REENABLES_DOCKER_RESTART")
    if not unit["prestart_present"] or not unit["start_present"]:
        reasons.append("SYSTEMD_PREFLIGHT_OR_START_MISSING")
    if not unit["enabled"]:
        reasons.append("SYSTEMD_NOT_ENABLED")
    if pending and unit["active"] == "active":
        reasons.append("PENDING_BUT_COMPOSE_SERVICE_ACTIVE")
    if reasons:
        status = "BOOT_OWNER_UNFENCED"
    elif pending:
        status = "PENDING_WRITER_FENCED_OBSERVED"
    else:
        status = "SINGLE_OWNER_POLICY_OBSERVED"
    return {
        "status": status,
        "reasons": reasons,
        "pending": pending,
        "docker_restart_policy": docker["restart_policy"],
        "docker_writer_running": docker["running"],
        "compose_restart_policy": compose["restart_policy"],
        "systemd_state": unit["active"],
        "systemd_enabled": unit["enabled"],
        "all_other_restart_owners_fenced": False,
        "offline_signature_verified_here": False,
        "schema_write_compatibility_proven": False,
        "boot_start_authorized": False,
        "source_install_authorized": False,
        "telegram_photo_authorized": False,
        "production_modified": False,
    }


def observe(container: str, file: Path, unit: str, marker: Path) -> dict:
    """Combine read-only state from Docker, Compose, systemd and a marker."""
    if not CONTAINER.fullmatch(container) or not SERVICE.fullmatch(unit):
        raise Blocked("TARGET_NOT_IN_ALLOWLIST")
    ensure_file(file)
    return classify(
        docker_state(container),
        compose_state(file, container),
        systemd_state(unit),
        pending_state(marker),
    )


def main() -> int:
    """Default dry-run; explicit paths and targets required for observing."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observe", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--container", default="")
    parser.add_argument("--compose", type=Path)
    parser.add_argument("--unit", default="")
    parser.add_argument("--pending", type=Path)
    parser.add_argument("--jsonl", action="store_true")
    args = parser.parse_args()
    if args.dry_run or not args.observe:
        print("DRY-RUN PASS: no Docker, systemd or SQLite inspected")
        return 0
    try:
        if args.compose is None or args.pending is None:
            raise Blocked("ALL_TARGETS_REQUIRED")
        report = observe(
            args.container,
            args.compose,
            args.unit,
            args.pending,
        )
    except (Blocked, OSError, ValueError, TypeError) as exc:
        report = {
            "status": "BLOCKED",
            "reasons": [str(exc)],
            "boot_start_authorized": False,
            "source_install_authorized": False,
            "telegram_photo_authorized": False,
            "production_modified": False,
        }
    if args.jsonl:
        print(json.dumps(report, separators=(",", ":")))
    else:
        print("BOOT OWNER OBSERVER:", report["status"])
        for reason in report["reasons"]:
            print(" -", reason)
        print("NO BOOT OR PRODUCTION AUTHORIZATION")
    return 0 if report["status"].endswith("_OBSERVED") else 2


if __name__ == "__main__":
    raise SystemExit(main())
