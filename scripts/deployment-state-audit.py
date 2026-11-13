#!/usr/bin/env python3
"""Audit signed-image deployment parity without modifying production."""

from __future__ import annotations

import argparse
import json
import re
import stat
import subprocess
from pathlib import Path

VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+-hw")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
IMAGE = "ghcr.io/isentinel/shelly-voltage-telegram"
CONTAINER = "shelly-voltage-bot"


class AuditBlocked(Exception):
    """Carry a safe reason code without exposing local secrets."""


def read_small(path: Path, limit: int = 4096, private: bool = False) -> str:
    """Reject unsafe files and unbounded or linked local input."""
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise AuditBlocked("LOCAL_FILE_LINKED")
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise AuditBlocked("LOCAL_FILE_UNSAFE")
        if info.st_size < 1 or info.st_size > limit:
            raise AuditBlocked("LOCAL_FILE_SIZE")
        if private and stat.S_IMODE(info.st_mode) != 0o600:
            raise AuditBlocked("PRIVATE_STATE_UNSAFE")
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise AuditBlocked("LOCAL_FILE_UNAVAILABLE") from exc


def version_marker(root: Path) -> str:
    """Require both source version markers to agree."""
    source = read_small(root / "VERSION", limit=128).strip()
    release = read_small(root / "RELEASE_VERSION", limit=128).strip()
    if not VERSION.fullmatch(source) or release != source:
        raise AuditBlocked("SOURCE_MARKERS_INCONSISTENT")
    return source


def deployed_record(state: Path) -> tuple[str, str]:
    """Read the root-protected release and immutable OCI digest."""
    try:
        record = json.loads(read_small(state / "deployed.json", private=True))
        version = record["version"]
        digest = record["digest"]
    except (TypeError, ValueError, KeyError) as exc:
        raise AuditBlocked("DEPLOYED_MARKER_INVALID") from exc
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise AuditBlocked("DEPLOYED_VERSION_INVALID")
    if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
        raise AuditBlocked("DEPLOYED_DIGEST_INVALID")
    return version, digest


def override_image(state: Path) -> str:
    """Extract the single pinned image without loading arbitrary YAML."""
    content = read_small(state / "override.yaml", private=True)
    matches = re.findall(r"(?m)^\s*image:\s*(\S+)\s*$", content)
    if len(matches) != 1:
        raise AuditBlocked("IMAGE_OVERRIDE_INVALID")
    return matches[0]


def inspect_running_image() -> tuple[str, str, str]:
    """Read Docker image and health without any mutation operation."""
    command = [
        "docker",
        "inspect",
        "--format",
        "|".join(
            [
                "{{.Config.Image}}",
                "{{.State.Health.Status}}",
                "{{.HostConfig.RestartPolicy.Name}}",
            ]
        ),
        CONTAINER,
    ]
    try:
        result = subprocess.run(
            command, text=True, capture_output=True, timeout=8, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AuditBlocked("DOCKER_INSPECT_UNAVAILABLE") from exc
    if result.returncode != 0:
        raise AuditBlocked("DOCKER_INSPECT_FAILED")
    fields = result.stdout.strip().split("|")
    if len(fields) != 3:
        raise AuditBlocked("DOCKER_INSPECT_INVALID")
    return fields[0], fields[1], fields[2]


def audit(root: Path, state: Path) -> dict:
    """Report all safely observable release parity blockers."""
    reasons: list[str] = []
    source = deployed = digest = None
    try:
        source = version_marker(root)
        deployed, digest = deployed_record(state)
        if source != deployed:
            reasons.append("SOURCE_IMAGE_VERSION_MISMATCH")
        if (state / "pending-update").exists() or (
            state / "pending-update"
        ).is_symlink():
            reasons.append("UPDATE_PENDING")
        if override_image(state) != f"{IMAGE}@{digest}":
            reasons.append("IMAGE_OVERRIDE_MISMATCH")
        image, health, restart = inspect_running_image()
        if image != f"{IMAGE}@{digest}":
            reasons.append("RUNNING_IMAGE_MISMATCH")
        if health != "healthy" or restart != "unless-stopped":
            reasons.append("RUNTIME_NOT_HEALTHY")
    except AuditBlocked as exc:
        reasons.append(str(exc))
    return {
        "status": "BLOCKED" if reasons else "PASS",
        "source_version": source,
        "deployed_version": deployed,
        "reason_codes": reasons,
        "production_modified": False,
        "signature_verified_here": False,
        "rollout_authorized": False,
    }


def main() -> int:
    """Provide bounded, ID-free JSON or plain PASS/FAIL for operators."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path("/opt/shelly-voltage")
    )
    parser.add_argument(
        "--state", type=Path, default=Path("/var/lib/shelly-voltage-updater")
    )
    parser.add_argument("--jsonl", action="store_true")
    args = parser.parse_args()
    result = audit(args.root, args.state)
    if args.jsonl:
        print(json.dumps(result, separators=(",", ":")))
    else:
        print(f"DEPLOYMENT PARITY: {result['status']}")
        for reason in result["reason_codes"]:
            print(f" - {reason}")
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
