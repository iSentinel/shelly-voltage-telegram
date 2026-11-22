#!/usr/bin/env python3
"""Identify an existing stopped container eligible for signed retained boot."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ID = re.compile(r"(?:sha256:)?[0-9a-f]{64}")
ENV_PATH = (
    '{{range .Config.Env}}{{if eq (index (split . "=") 0) "DATABASE_PATH"}}'
    "{{println .}}{{end}}{{end}}"
)
CONTAINER_FORMAT = "|".join(
    (
        "{{.Id}}",
        "{{.Config.Image}}",
        "{{.Image}}",
        "{{.State.Status}}",
        "{{.HostConfig.RestartPolicy.Name}}",
        "{{json .Mounts}}",
        ENV_PATH,
    )
)


class BootBlocked(ValueError):
    """Carry a fixed diagnostic code without logging deployment data."""


def deployment_audit():
    """Reuse the signed deployment receipt readers."""

    path = Path(__file__).with_name("deployment-state-audit.py")
    spec = importlib.util.spec_from_file_location("retained_boot_audit", path)
    if spec is None or spec.loader is None:
        raise BootBlocked("AUDIT_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_owner_receipt(path: Path, audit) -> str:
    """Read one private file belonging to the invoking updater owner."""

    text = audit.read_small(path, private=True)
    if path.stat().st_uid != os.geteuid():
        raise BootBlocked("RECEIPT_OWNER_INVALID")
    return text


def retained_release(
    root: Path, state: Path, snapshot: Path, audit
) -> tuple[str, str, str]:
    """Check that the original deployed image selection is still in place."""

    marker = read_owner_receipt(state / "pending-update", audit)
    if (
        snapshot.parent != state / "snapshots"
        or marker.strip() != snapshot.name
    ):
        raise BootBlocked("PENDING_SNAPSHOT_CHANGED")
    for name in ("override.yaml", "deployed.json"):
        if read_owner_receipt(state / name, audit) != read_owner_receipt(
            snapshot / name, audit
        ):
            raise BootBlocked("DEPLOYMENT_SELECTION_CHANGED")
    version, digest = audit.deployed_record(state)
    if audit.version_marker(root) != version:
        raise BootBlocked("SOURCE_VERSION_MISMATCH")
    if audit.override_image(state) != f"{audit.IMAGE}@{digest}":
        raise BootBlocked("IMAGE_SELECTION_MISMATCH")
    identifier = read_owner_receipt(snapshot / "container-id", audit).strip()
    if not ID.fullmatch(identifier) or identifier.startswith("sha256:"):
        raise BootBlocked("SNAPSHOT_CONTAINER_INVALID")
    return version, digest, identifier


def docker_inspect(*args: str) -> str:
    """Read only the fields needed to identify the retained container."""

    result = subprocess.run(
        ["docker", *args],
        capture_output=True,
        text=True,
        timeout=8,
        check=False,
    )
    if result.returncode or len(result.stdout) > 32768:
        raise BootBlocked("DOCKER_INSPECT_FAILED")
    return result.stdout.strip()


def binds_database(mounts: list[dict], database: Path) -> bool:
    """Require the single normal data mount and prevent path shadowing."""

    candidates = [
        item
        for item in mounts
        if item.get("Destination") == "/data"
        or str(item.get("Destination", "")).startswith("/data/")
    ]
    return (
        len(candidates) == 1
        and candidates[0].get("Destination") == "/data"
        and candidates[0].get("Type") == "bind"
        and candidates[0].get("Source") == str(database.parent)
        and candidates[0].get("RW") is True
    )


def retained_container(reference: str, name: str, database: Path) -> str:
    """Accept only the existing stopped container backed by the exact image."""

    fields = docker_inspect(
        "inspect", "--format", CONTAINER_FORMAT, name
    ).split("|")
    if len(fields) != 7:
        raise BootBlocked("CONTAINER_STATE_INVALID")
    identifier, selected, image, status, policy, mounts, db_env = fields
    if (
        not ID.fullmatch(identifier)
        or identifier.startswith("sha256:")
        or not ID.fullmatch(image)
        or selected != reference
        or status != "exited"
        or policy != "no"
        or db_env != f"DATABASE_PATH=/data/{database.name}"
    ):
        raise BootBlocked("CONTAINER_STATE_MISMATCH")
    if not binds_database(json.loads(mounts), database):
        raise BootBlocked("DATABASE_MOUNT_MISMATCH")
    actual = docker_inspect(
        "image", "inspect", "--format", "{{.Id}}", reference
    )
    if actual != image:
        raise BootBlocked("LOCAL_IMAGE_MISMATCH")
    return identifier


def main() -> int:
    """Print verified identifiers for the updater or a safe diagnostic."""

    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("root", "state", "snapshot", "db"):
        parser.add_argument(f"--{option}", required=True, type=Path)
    args = parser.parse_args()
    try:
        audit = deployment_audit()
    except (BootBlocked, OSError):
        print(
            "RETAINED BOOT BLOCKED: deployment audit unavailable",
            file=sys.stderr,
        )
        return 2
    try:
        version, digest, previous = retained_release(
            args.root, args.state, args.snapshot, audit
        )
        container = retained_container(
            f"{audit.IMAGE}@{digest}", audit.CONTAINER, args.db.absolute()
        )
        if container != previous:
            raise BootBlocked("CONTAINER_REPLACED")
    except BootBlocked as exc:
        print(f"RETAINED BOOT BLOCKED: {exc}", file=sys.stderr)
        return 2
    except (
        audit.AuditBlocked,
        OSError,
        ValueError,
        TypeError,
        AttributeError,
        subprocess.TimeoutExpired,
    ):
        print("RETAINED BOOT BLOCKED: evidence unavailable", file=sys.stderr)
        return 2
    print(version, digest, container)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
