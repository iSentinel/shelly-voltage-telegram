#!/usr/bin/env python3
"""Audit Docker/systemd writer restart hazards; never stop any service."""

from __future__ import annotations

import argparse
import json
import subprocess
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

COMPOSE_UNIT = "shelly-voltage-compose.service"
UPDATE_TIMER = "shelly-voltage-update-check.timer"
CONTAINER = "shelly-voltage-bot"
READY_URL = "http://127.0.0.1:8080/ready"
FIELDS = ("LoadState", "ActiveState", "UnitFileState")


class Blocked(Exception):
    """Explain failures with stable, non-secret reason codes."""


def command_output(argv: list[str]) -> str:
    """Run one fixed read-only command with bounded output and timeout."""
    try:
        result = subprocess.run(
            argv, capture_output=True, text=True, timeout=8, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Blocked("LOCAL_INSPECTION_UNAVAILABLE") from exc
    if result.returncode or len(result.stdout) > 4096:
        raise Blocked("LOCAL_INSPECTION_FAILED")
    return result.stdout.strip()


def inspect_unit(name: str) -> dict[str, str]:
    """Read only service availability, startup and activity attributes."""
    keys = FIELDS + (("Type",) if name == COMPOSE_UNIT else ())
    cmd = ["systemctl", "show", name, "--no-pager"]
    cmd.extend(f"--property={key}" for key in keys)
    fields = {}
    for line in command_output(cmd).splitlines():
        key, sep, value = line.partition("=")
        if sep and key in keys and key not in fields:
            fields[key] = value.strip()
    if any(key not in fields for key in keys):
        raise Blocked("SYSTEMD_UNIT_PROPERTIES_MISSING")
    return fields


def inspect_container() -> dict[str, str]:
    """Request exactly three safe attributes from one named container."""
    fmt = (
        "{{.State.Running}}|{{.HostConfig.RestartPolicy.Name}}"
        "|{{.Config.Image}}"
    )
    output = command_output(["docker", "inspect", "--format", fmt, CONTAINER])
    parts = output.split("|")
    if len(parts) != 3 or parts[0] not in ("true", "false"):
        raise Blocked("DOCKER_STATE_INVALID")
    return {"running": parts[0], "restart": parts[1], "image": parts[2]}


def ready_code() -> int:
    """Probe only localhost and classify an expected offline HTTP 503."""
    try:
        with urlopen(READY_URL, timeout=4) as response:
            return int(response.status)
    except HTTPError as exc:
        code = exc.code
        exc.close()
        return int(code)
    except (URLError, OSError, ValueError) as exc:
        raise Blocked("READY_PROBE_UNAVAILABLE") from exc


def restart_hazards(
    service: dict[str, str],
    timer: dict[str, str],
    container: dict[str, str],
    ready: int,
) -> list[str]:
    """Name risks that invalidate a stopped-writer claim."""
    reasons: list[str] = []
    if service.get("LoadState") != "loaded":
        reasons.append("COMPOSE_SERVICE_UNVERIFIED")
    if service.get("ActiveState") == "active":
        reasons.append("COMPOSE_SERVICE_ACTIVE")
    if service.get("UnitFileState") == "enabled":
        reasons.append("COMPOSE_STARTUP_ENABLED")
    if container.get("running") != "false":
        reasons.append("DOCKER_WRITER_NOT_STOPPED")
    if container.get("restart") in ("always", "unless-stopped"):
        reasons.append("DOCKER_RESTART_POLICY_REQUIRES_REVIEW")
    if timer.get("UnitFileState") == "enabled":
        reasons.append("UPDATER_TIMER_ENABLED")
    if timer.get("ActiveState") == "active":
        reasons.append("UPDATER_TIMER_ACTIVE")
    if ready != 200:
        reasons.append("SHELLY_NOT_READY")
    if not container.get("image", "").startswith(
        "ghcr.io/isentinel/shelly-voltage-telegram@sha256:"
    ):
        reasons.append("SIGNED_IMAGE_IDENTITY_UNVERIFIED")
    return reasons


def decide(
    service: dict[str, str],
    timer: dict[str, str],
    container: dict[str, str],
    ready: int,
) -> dict:
    """Describe restart hazards without authorizing any live maintenance."""
    reasons = restart_hazards(service, timer, container, ready)
    return {
        "status": "BLOCKED" if reasons else "READ_ONLY_PREFLIGHT_PASS",
        "reasons": reasons,
        "service_state": service.get("ActiveState"),
        "container_running": container.get("running"),
        "docker_restart_policy": container.get("restart"),
        "updater_timer_state": timer.get("ActiveState"),
        "ready_http_status": ready,
        "writer_fenced": False,
        "owner_maintenance_authorized": False,
        "production_modified": False,
        "public_photo_authorized": False,
    }


def audit() -> dict:
    """Collect non-secret runtime states and return a fail-closed verdict."""
    try:
        return decide(
            inspect_unit(COMPOSE_UNIT),
            inspect_unit(UPDATE_TIMER),
            inspect_container(),
            ready_code(),
        )
    except Blocked as exc:
        return {
            "status": "BLOCKED",
            "reasons": [str(exc)],
            "writer_fenced": False,
            "owner_maintenance_authorized": False,
            "production_modified": False,
            "public_photo_authorized": False,
        }


def main() -> int:
    """Return a read-only snapshot without claiming writer fencing."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jsonl", action="store_true")
    args = parser.parse_args()
    report = audit()
    if args.jsonl:
        print(json.dumps(report, separators=(",", ":")))
    else:
        print(f"WRITER RESTART FENCE AUDIT: {report['status']}")
        for reason in report["reasons"]:
            print(" -", reason)
        print("NO WRITER STOP; NO SOURCE INSTALL; NO TELEGRAM MUTATION")
    return 0 if report["status"] == "READ_ONLY_PREFLIGHT_PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
