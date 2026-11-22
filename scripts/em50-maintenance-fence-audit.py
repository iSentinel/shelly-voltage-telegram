#!/usr/bin/env python3
"""Read-only signed single-writer maintenance preconditions."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import stat
import subprocess
from pathlib import Path

IMAGE_PREFIX = "ghcr.io/isentinel/shelly-voltage-telegram@"
VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+-hw$")


class AuditBlocked(Exception):
    """Fail-closed reason safe for an operator or CI log."""


def require_plain_path(path: Path) -> None:
    """Reject symlink components before checking private release state."""

    if not path.is_absolute():
        raise AuditBlocked("PATH_NOT_ABSOLUTE")
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise AuditBlocked("SYMLINKED_STATE_PATH")
    if not path.is_file() or path.stat().st_nlink != 1:
        raise AuditBlocked("STATE_FILE_UNAVAILABLE")


def release_identity(project: Path, state: Path) -> str:
    """Require both on-disk markers to match the signed updater receipt."""

    markers = (project / "VERSION", project / "RELEASE_VERSION")
    receipt = state / "deployed.json"
    for file in (*markers, receipt):
        require_plain_path(file)
    versions = [marker.read_text().strip() for marker in markers]
    try:
        deployed = json.loads(receipt.read_text())
    except (OSError, ValueError) as exc:
        raise AuditBlocked("INVALID_DEPLOYED_RECEIPT") from exc
    if not isinstance(deployed, dict):
        raise AuditBlocked("INVALID_DEPLOYED_RECEIPT")
    version, digest = deployed.get("version"), deployed.get("digest")
    if (
        len(set(versions)) != 1
        or not VERSION_PATTERN.fullmatch(versions[0])
        or version != versions[0]
        or not isinstance(digest, str)
        or not re.fullmatch(r"sha256:[a-f0-9]{64}", digest)
    ):
        raise AuditBlocked("SOURCE_OR_RECEIPT_MISMATCH")
    pending = state / "pending-update"
    if pending.exists() or pending.is_symlink():
        raise AuditBlocked("UPDATER_PENDING")
    return IMAGE_PREFIX + digest


def check_lock_available(lock: Path) -> None:
    """Probe updater lock non-blockingly without writing any disk bytes."""

    require_plain_path(lock)
    try:
        with lock.open("rb") as file:
            if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
                raise AuditBlocked("INVALID_LOCK_FILE")
            fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(file.fileno(), fcntl.LOCK_UN)
    except BlockingIOError as exc:
        raise AuditBlocked("UPDATER_LOCK_HELD") from exc
    except OSError as exc:
        raise AuditBlocked("UPDATER_LOCK_UNAVAILABLE") from exc


def docker_state(name: str) -> tuple[str, str, str]:
    """Inspect without starting, stopping or changing a Docker container."""

    result = subprocess.run(
        [
            "docker",
            "inspect",
            "--format",
            "{{.Config.Image}}|{{.State.Running}}|"
            "{{.HostConfig.RestartPolicy.Name}}",
            name,
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode != 0:
        raise AuditBlocked("DOCKER_INSPECT_FAILED")
    parts = result.stdout.strip().split("|")
    if len(parts) != 3 or parts[1] not in ("true", "false"):
        raise AuditBlocked("DOCKER_STATE_INVALID")
    return parts[0], parts[1], parts[2]


def check_phase(phase: str, running: str, restart: str) -> None:
    """Match the real writer and restart fence to the selected lifecycle."""

    if phase == "live":
        if running != "true" or restart != "unless-stopped":
            raise AuditBlocked("LIVE_WRITER_OR_RESTART_POLICY_INVALID")
    elif phase == "fenced":
        if running != "false" or restart != "no":
            raise AuditBlocked("WRITER_NOT_STOPPED_AND_FENCED")
    else:
        if running != "false" or restart not in ("no", "unless-stopped"):
            raise AuditBlocked("WRITER_NOT_STOPPED_BEFORE_START")


def audit(args: argparse.Namespace) -> dict:
    """Return verified preconditions; never authorize production deployment."""

    expected = release_identity(args.project_root, args.state)
    check_lock_available(args.lock)
    image, running, restart = docker_state(args.container)
    if image != expected:
        raise AuditBlocked("RUNNING_IMAGE_MISMATCH")
    check_phase(args.phase, running, restart)
    return {
        "status": "PASS",
        "phase": args.phase,
        "writer_running": running == "true",
        "restart_policy": restart,
        "updater_lock_available": True,
        "source_matches_signed_receipt": True,
        "read_only": True,
        "restart_or_update_performed": False,
        "release_authorized": False,
    }


def options() -> argparse.Namespace:
    """Parse an explicit lifecycle phase and read-only source paths."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--phase", choices=("live", "fenced", "before-start"), required=True
    )
    parser.add_argument(
        "--project-root", type=Path, default=Path("/opt/shelly-voltage")
    )
    parser.add_argument(
        "--state", type=Path, default=Path("/var/lib/shelly-voltage-updater")
    )
    parser.add_argument(
        "--lock",
        type=Path,
        default=Path("/run/lock/shelly-voltage-updater.lock"),
    )
    parser.add_argument("--container", default="shelly-voltage-bot")
    parser.add_argument("--jsonl", action="store_true")
    return parser.parse_args()


def main() -> int:
    """Provide stable PASS/FAIL and prevent unsafe maintenance transitions."""

    args = options()
    try:
        result = audit(args)
    except (
        AuditBlocked,
        OSError,
        ValueError,
        subprocess.TimeoutExpired,
    ) as exc:
        reason = (
            str(exc) if isinstance(exc, AuditBlocked) else type(exc).__name__
        )
        result = {
            "status": "FAIL",
            "phase": args.phase,
            "reason": reason,
            "read_only": True,
            "restart_or_update_performed": False,
            "release_authorized": False,
        }
    if args.jsonl:
        print(json.dumps(result, sort_keys=True))
    else:
        print(result["status"], result.get("reason", args.phase))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
