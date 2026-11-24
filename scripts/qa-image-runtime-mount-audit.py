#!/usr/bin/env python3
"""Audit a Docker mount inventory for forbidden host application code."""

from __future__ import annotations

import argparse
import json
import os
import stat
from pathlib import Path

MANDATORY = {
    "/data": ("/var/lib/shelly-voltage", True),
    "/run/yasno": ("/var/lib/shelly-yasno-reader", False),
    "/var/backups/shelly-voltage": ("/var/backups/shelly-voltage", False),
}
SECRETS = {
    "shelly_password",
    "shelly_l2_password",
    "shelly_l3_password",
    "telegram_chat_id",
    "telegram_token",
}


class MountBlocked(ValueError):
    """An untrusted or code-overriding bind mount prevents acceptance."""


def arguments() -> argparse.Namespace:
    """Use an explicit QA-only Docker mount inventory JSON file."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qa-only", action="store_true", required=True)
    parser.add_argument("--mounts-json", type=Path, required=True)
    parser.add_argument("--jsonl", action="store_true")
    return parser.parse_args()


def read_mounts(path: Path) -> list:
    """Read only a bounded regular file, never a complete Docker inspect."""
    if (
        not path.is_absolute()
        or ".." in path.parts
        or any(part.is_symlink() for part in (path, *path.parents))
    ):
        raise MountBlocked("MOUNT_INVENTORY_PATH_INVALID")
    try:
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        with os.fdopen(os.open(path, flags), "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or not 1 <= info.st_size <= 128 * 1024
            ):
                raise MountBlocked("MOUNT_INVENTORY_FILE_UNSAFE")
            data = json.loads(stream.read(128 * 1024 + 1))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MountBlocked("MOUNT_INVENTORY_UNAVAILABLE") from exc
    if not isinstance(data, list) or not 3 <= len(data) <= 24:
        raise MountBlocked("MOUNT_INVENTORY_STRUCTURE_INVALID")
    return data


def allowed_mount(destination: str) -> tuple[str, bool]:
    """Accept only known data, read-only YASNO, backup and secret paths."""
    if destination in MANDATORY:
        return MANDATORY[destination]
    prefix = "/run/secrets/"
    if destination.startswith(prefix):
        secret = destination[len(prefix) :]
        if secret in SECRETS:
            return ("/opt/shelly-voltage/secrets/" + secret, False)
    raise MountBlocked("MOUNT_DESTINATION_NOT_ALLOWLISTED")


def check_mounts(mounts: list) -> dict:
    """Reject source substitution, duplicate targets and writable code."""
    destinations: set[str] = set()
    for mount in mounts:
        if not isinstance(mount, dict):
            raise MountBlocked("MOUNT_ENTRY_INVALID")
        destination = mount.get("Destination")
        if not isinstance(destination, str) or destination in destinations:
            raise MountBlocked("MOUNT_DESTINATION_DUPLICATE_OR_INVALID")
        destinations.add(destination)
        source, writable = allowed_mount(destination)
        if (
            mount.get("Type") != "bind"
            or mount.get("Source") != source
            or mount.get("RW") is not writable
        ):
            raise MountBlocked("MOUNT_SOURCE_OR_MODE_MISMATCH")
    if not MANDATORY.keys() <= destinations:
        raise MountBlocked("MOUNT_REQUIRED_DATA_SOURCE_MISSING")
    return {
        "status": "PASS_RUNTIME_MOUNT_LAYOUT_EVIDENCE_ONLY",
        "code_bind_mounts_absent": True,
        "data_write_mount_present": True,
        "secret_mount_count": len(destinations) - len(MANDATORY),
        "mount_source_path_trust_verified": False,
        "old_oci_signature_verified": False,
        "rollback_authorized": False,
        "release_authorized": False,
        "production_modified": False,
    }


def main() -> int:
    """Emit advisory layout evidence without Docker or SQLite access."""
    args = arguments()
    try:
        if not args.qa_only:
            raise MountBlocked("MOUNT_QA_ONLY_REQUIRED")
        report = check_mounts(read_mounts(args.mounts_json))
    except (MountBlocked, TypeError) as exc:
        report = {
            "status": "BLOCKED",
            "reason": (
                str(exc)
                if isinstance(exc, MountBlocked)
                else "MOUNT_INVENTORY_INVALID"
            ),
            "rollback_authorized": False,
            "release_authorized": False,
            "production_modified": False,
        }
    if args.jsonl:
        print(json.dumps(report, separators=(",", ":")))
    else:
        print("RUNTIME MOUNT AUDIT:", report["status"])
        if report["status"] == "BLOCKED":
            print("reason:", report["reason"])
    return 0 if report["status"].startswith("PASS_") else 3


if __name__ == "__main__":
    raise SystemExit(main())
