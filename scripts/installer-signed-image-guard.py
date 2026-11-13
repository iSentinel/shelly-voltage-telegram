#!/usr/bin/env python3
"""Prevent replacing an immutable signed image during source upgrades."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import stat
import subprocess
from pathlib import Path

DIGEST_PREFIX = "ghcr.io/isentinel/shelly-voltage-telegram@"
AUDIT_SCRIPT = Path(__file__).with_name("deployment-state-audit.py")


class GuardBlocked(Exception):
    """Safe reason code without credential or private metadata exposure."""


def load_audit():
    """Reuse exactly the same source and private-state checks as parity QA."""
    spec = importlib.util.spec_from_file_location(
        "source_parity", AUDIT_SCRIPT
    )
    if spec is None or spec.loader is None:
        raise GuardBlocked("PARITY_AUDIT_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def inspect_container(dry_run: bool) -> tuple[str, str, str]:
    """Check actual image identity; require stopped and fenced writer."""
    command = [
        "docker",
        "inspect",
        "--format",
        "|".join(
            [
                "{{.Config.Image}}",
                "{{.State.Running}}",
                "{{.HostConfig.RestartPolicy.Name}}",
            ]
        ),
        "shelly-voltage-bot",
    ]
    try:
        result = subprocess.run(
            command, check=False, text=True, capture_output=True, timeout=8
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GuardBlocked("DOCKER_INSPECT_UNAVAILABLE") from exc
    if result.returncode != 0:
        raise GuardBlocked("DOCKER_INSPECT_FAILED")
    fields = result.stdout.strip().split("|")
    if len(fields) != 3:
        raise GuardBlocked("DOCKER_INSPECT_INVALID")
    if not dry_run and fields[1:] != ["false", "no"]:
        raise GuardBlocked("WRITER_NOT_STOPPED_AND_FENCED")
    return tuple(fields)


def check_private_state(state: Path) -> None:
    """Require an owner-private directory and regular updater state."""
    state_info = state.lstat()
    if (
        not stat.S_ISDIR(state_info.st_mode)
        or stat.S_IMODE(state_info.st_mode) != 0o700
        or state_info.st_uid != os.geteuid()
    ):
        raise GuardBlocked("SIGNED_STATE_DIRECTORY_UNSAFE")
    for filename in ("deployed.json", "override.yaml"):
        if (state / filename).stat().st_uid != os.geteuid():
            raise GuardBlocked("SIGNED_STATE_OWNER_MISMATCH")


def inspect(source: Path, state: Path, dry_run: bool) -> dict:
    """Verify exact signed receipt and old container before any source copy."""
    audit = load_audit()
    try:
        check_private_state(state)
        version = audit.version_marker(source)
        deployed, digest = audit.deployed_record(state)
        override = audit.override_image(state)
    except audit.AuditBlocked as exc:
        raise GuardBlocked(str(exc)) from exc
    except OSError as exc:
        raise GuardBlocked("SIGNED_STATE_UNAVAILABLE") from exc
    if version != deployed:
        raise GuardBlocked("SOURCE_SIGNED_VERSION_MISMATCH")
    if (state / "pending-update").exists() or (
        state / "pending-update"
    ).is_symlink():
        raise GuardBlocked("UPDATE_PENDING")
    expected = DIGEST_PREFIX + digest
    if override != expected:
        raise GuardBlocked("IMAGE_OVERRIDE_MISMATCH")
    image, running, policy = inspect_container(dry_run)
    if image != expected:
        raise GuardBlocked("RUNNING_IMAGE_MISMATCH")
    return {
        "status": "PASS",
        "source_version": version,
        "signed_image_pinned": True,
        "stopped_and_fenced": running == "false" and policy == "no",
        "production_modified": False,
        "signature_verified_here": False,
        "rollout_authorized": False,
    }


def main() -> int:
    """Report safe identity validation before installer mutations."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument(
        "--state",
        type=Path,
        default=Path("/var/lib/shelly-voltage-updater"),
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--jsonl", action="store_true")
    args = parser.parse_args()
    try:
        result = inspect(args.source, args.state, args.dry_run)
    except GuardBlocked as exc:
        result = {
            "status": "BLOCKED",
            "reason": str(exc),
            "production_modified": False,
            "rollout_authorized": False,
        }
    if args.jsonl:
        print(json.dumps(result, separators=(",", ":")))
    else:
        print("SIGNED IMAGE INSTALL GUARD:", result["status"])
        if result["status"] != "PASS":
            print("reason:", result["reason"])
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
