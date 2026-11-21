#!/usr/bin/env python3
"""Observe selected legacy restart owners in disposable QA only."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

from cutover_journal import Journal, JournalBlocked, digest

QA_NAME = re.compile(r"svt-qa-[a-zA-Z0-9_-]{1,80}")
IMAGE = re.compile(r"sha256:[0-9a-f]{64}")
CONTAINER_ID = re.compile(r"[0-9a-f]{64}")
UNIT_FIELDS = (
    "LoadState",
    "ActiveState",
    "UnitFileState",
    "NeedDaemonReload",
    "Job",
)


def query(command: list[str]) -> str:
    """Read bounded live evidence without reporting environment data."""
    result = subprocess.run(
        command, capture_output=True, text=True, timeout=10, check=False
    )
    if result.returncode or len(result.stdout) > 8192:
        raise JournalBlocked("LEGACY_OBSERVATION_UNAVAILABLE")
    return result.stdout.strip()


def validate_scope(args: argparse.Namespace) -> None:
    """Restrict this observation to the named disposable QA environment."""
    if (
        args.qa_only is not True
        or not QA_NAME.fullmatch(args.container)
        or not QA_NAME.fullmatch(args.unit.removesuffix(".service"))
        or not args.unit.endswith(".service")
        or not IMAGE.fullmatch(args.image)
        or query(["hostname"]) != "shelly-docker-reboot-qa"
    ):
        raise JournalBlocked("LEGACY_OBSERVATION_QA_REQUIRED")


def unit_state(unit: str) -> dict:
    """Read the loaded unit properties and check the persistent mask."""
    raw = query(
        [
            "systemctl",
            "show",
            unit,
            "--no-pager",
            "--property=" + ",".join(UNIT_FIELDS),
        ]
    )
    fields = {}
    for line in raw.splitlines():
        key, sep, val = line.partition("=")
        if not sep or key not in UNIT_FIELDS or key in fields:
            raise JournalBlocked("LEGACY_UNIT_FIELDS_INVALID")
        fields[key] = val
    if set(fields) != set(UNIT_FIELDS):
        raise JournalBlocked("LEGACY_UNIT_FIELDS_INCOMPLETE")
    path = Path("/etc/systemd/system") / unit
    fields["persistent_mask"] = path.is_symlink() and path.readlink() == Path(
        "/dev/null"
    )
    return fields


def container_state(name: str) -> dict:
    """Inspect one container ID, image, restart policy and running state."""
    raw = query(
        [
            "docker",
            "container",
            "inspect",
            name,
            "--format",
            "{{json .Id}}|{{json .Image}}|"
            "{{json .HostConfig.RestartPolicy.Name}}|"
            "{{json .State.Running}}|{{json .State.Restarting}}|"
            "{{json .State.Status}}|{{json .HostConfig.NetworkMode}}",
        ]
    )
    try:
        values = [json.loads(item) for item in raw.split("|")]
    except ValueError as exc:
        raise JournalBlocked("LEGACY_CONTAINER_FIELDS_INVALID") from exc
    keys = (
        "id",
        "image",
        "restart",
        "running",
        "restarting",
        "state",
        "network",
    )
    if len(values) != len(keys):
        raise JournalBlocked("LEGACY_CONTAINER_FIELDS_INVALID")
    return dict(zip(keys, values, strict=True))


def check_inhibition(unit: dict, container: dict, image: str) -> None:
    """Reject a volatile mask, queued job, identity drift or auto-restart."""
    if (
        unit.get("persistent_mask") is not True
        or unit.get("LoadState") != "masked"
        or unit.get("UnitFileState") != "masked"
        or unit.get("NeedDaemonReload") != "no"
        or unit.get("Job") not in ("", "0")
        or unit.get("ActiveState") not in ("active", "inactive")
    ):
        raise JournalBlocked("LEGACY_UNIT_NOT_PERSISTENTLY_INHIBITED")
    if (
        not isinstance(container.get("id"), str)
        or not CONTAINER_ID.fullmatch(container["id"])
        or container.get("image") != image
        or container.get("restart") != "no"
        or container.get("restarting") is not False
        or container.get("network") != "none"
        or type(container.get("running")) is not bool
    ):
        raise JournalBlocked("LEGACY_CONTAINER_NOT_INHIBITED")


def observe(args: argparse.Namespace) -> dict:
    """Re-read live evidence and refuse concurrent identity/state drift."""
    validate_scope(args)
    unit = unit_state(args.unit)
    container = container_state(args.container)
    check_inhibition(unit, container, args.image)
    if args.require_stopped and (
        unit["ActiveState"] != "inactive"
        or container["running"] is not False
        or container["state"] not in ("created", "exited")
    ):
        raise JournalBlocked("LEGACY_WRITER_NOT_STOPPED")
    if unit != unit_state(args.unit) or container != container_state(
        args.container
    ):
        raise JournalBlocked("LEGACY_OBSERVATION_CHANGED")
    return {
        "status": "PASS_QA_SELECTED_LEGACY_OWNER_OBSERVATION_ONLY",
        "stage": "stop_legacy_writer"
        if args.require_stopped
        else "inhibit_legacy_restarts",
        "unit": args.unit,
        "container": args.container,
        "container_id": container["id"],
        "container_image": container["image"],
        "persistent_mask": True,
        "restart_disabled": True,
        "selected_container_stopped": not container["running"],
        "all_writers_verified": False,
        "release_authorized": False,
        "production_modified": False,
    }


def record_observation(journal: Journal, args: argparse.Namespace) -> dict:
    """Record only the expected pending stage; never execute a stop."""
    stage = (
        "stop_legacy_writer"
        if args.require_stopped
        else "inhibit_legacy_restarts"
    )
    status = journal.inspect()
    if status["decision"] != "RECONCILE_REQUIRED" or status["stage"] != stage:
        raise JournalBlocked("LEGACY_OBSERVATION_INTENT_REQUIRED")
    report = observe(args)
    journal.append("observe", stage, digest(report))
    return report


def main() -> int:
    """Emit read-only QA evidence without production authorization."""
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--qa-only", action="store_true", required=True)
    cli.add_argument("--unit", required=True)
    cli.add_argument("--container", required=True)
    cli.add_argument("--image", required=True)
    cli.add_argument("--require-stopped", action="store_true")
    args = cli.parse_args()
    try:
        report = observe(args)
    except (ValueError, OSError, subprocess.TimeoutExpired):
        print(json.dumps({"status": "BLOCKED", "release_authorized": False}))
        return 3
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
