#!/usr/bin/env python3
"""Prevent source rollback from replacing an immutable signed GHCR image."""

from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
import tarfile
from pathlib import Path

HELPER = Path(__file__).with_name("installer-signed-image-guard.py")
IMAGE_PREFIX = "ghcr.io/isentinel/shelly-voltage-telegram@sha256:"


class Blocked(Exception):
    """Safe, non-secret rollback reason code."""


def load_guard():
    """Reuse the source installer's protected receipt checks."""
    spec = importlib.util.spec_from_file_location(
        "signed_rollback_shared", HELPER
    )
    if spec is None or spec.loader is None:
        raise Blocked("SIGNED_GUARD_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def archive_versions(snapshot: Path) -> tuple[str, str]:
    """Read only small version markers from a validated source archive."""
    values: dict[str, str] = {}
    try:
        with tarfile.open(snapshot / "source.tar.gz", "r:gz") as archive:
            for member in archive:
                if member.name not in ("./VERSION", "./RELEASE_VERSION"):
                    continue
                if member.name in values or not member.isfile():
                    raise Blocked("SNAPSHOT_VERSION_INVALID")
                if not 1 <= member.size <= 128:
                    raise Blocked("SNAPSHOT_VERSION_INVALID")
                stream = archive.extractfile(member)
                if stream is None:
                    raise Blocked("SNAPSHOT_VERSION_INVALID")
                values[member.name] = stream.read(129).decode("ascii").strip()
    except (OSError, UnicodeError, tarfile.TarError) as exc:
        raise Blocked("SNAPSHOT_VERSION_UNAVAILABLE") from exc
    if set(values) != {"./VERSION", "./RELEASE_VERSION"}:
        raise Blocked("SNAPSHOT_VERSION_MISSING")
    return values["./VERSION"], values["./RELEASE_VERSION"]


def docker_image() -> str:
    """Inspect actual container identity without changing Docker state."""
    try:
        result = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{.Config.Image}}",
                "shelly-voltage-bot",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Blocked("DOCKER_INSPECT_UNAVAILABLE") from exc
    if result.returncode != 0:
        raise Blocked("DOCKER_INSPECT_FAILED")
    return result.stdout.strip()


def check(snapshot: Path, state: Path) -> str:
    """Deny incompatible source restores before writer stop or SQLite edits."""
    guard = load_guard()
    actual = docker_image()
    receipt = state / "deployed.json"
    override = state / "override.yaml"
    present = any(
        path.exists() or path.is_symlink() for path in (receipt, override)
    )
    if not present:
        if actual.startswith(IMAGE_PREFIX):
            raise Blocked("SIGNED_IMAGE_WITHOUT_PROTECTED_RECEIPT")
        return "LEGACY"
    audit = guard.load_audit()
    try:
        guard.check_private_state(state)
        version, digest = audit.deployed_record(state)
        configured = audit.override_image(state)
    except (guard.GuardBlocked, audit.AuditBlocked) as exc:
        raise Blocked(str(exc)) from exc
    except (OSError, ValueError) as exc:
        raise Blocked("SIGNED_STATE_UNAVAILABLE") from exc
    if (state / "pending-update").exists() or (
        state / "pending-update"
    ).is_symlink():
        raise Blocked("UPDATE_PENDING")
    expected = guard.DIGEST_PREFIX + digest
    if configured != expected or actual != expected:
        raise Blocked("SIGNED_IMAGE_DIGEST_MISMATCH")
    source, release = archive_versions(snapshot)
    if source != version or release != version:
        raise Blocked("SNAPSHOT_SIGNED_VERSION_MISMATCH")
    return "SIGNED"


def main() -> int:
    """Return a safe mode token or fail closed with a reason code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument(
        "--state",
        type=Path,
        default=Path("/var/lib/shelly-voltage-updater"),
    )
    args = parser.parse_args()
    try:
        print(check(args.snapshot, args.state))
    except Blocked as exc:
        print(f"SIGNED ROLLBACK GUARD: BLOCKED {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
