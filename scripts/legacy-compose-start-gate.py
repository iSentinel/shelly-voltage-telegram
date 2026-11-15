#!/usr/bin/env python3
"""Read a short-lived gated-only Compose permit before any Docker call."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import stat
import time
from pathlib import Path

IMAGE = re.compile(
    r"ghcr\.io/isentinel/shelly-voltage-telegram@sha256:[0-9a-f]{64}"
)
COMPOSE_IMAGE = re.compile(
    r"(?m)^\s*image:\s*"
    r"(ghcr\.io/isentinel/shelly-voltage-telegram@sha256:[0-9a-f]{64})"
    r"\s*$"
)


class AdmissionDenied(RuntimeError):
    """Never treat an absent or untrusted owner permit as consent."""


def trusted_dir(path: Path) -> None:
    """Reject symlinked, nonroot or writable admission parents."""
    if not path.is_absolute() or path.resolve() != path:
        raise AdmissionDenied("UNTRUSTED_ADMISSION_DIRECTORY")
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0:
        raise AdmissionDenied("UNTRUSTED_ADMISSION_DIRECTORY")
    if stat.S_IMODE(info.st_mode) != 0o700:
        raise AdmissionDenied("UNTRUSTED_ADMISSION_DIRECTORY")


def trusted_fd(path: Path, mode: int) -> int:
    """Open only root-owned regular inode with exact private permissions."""
    if path.is_symlink() or path.resolve() != path:
        raise AdmissionDenied("UNTRUSTED_ADMISSION_FILE")
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        current = os.fstat(fd)
        named = path.lstat()
        if (
            not stat.S_ISREG(current.st_mode)
            or current.st_uid != 0
            or current.st_nlink != 1
            or stat.S_IMODE(current.st_mode) != mode
            or (current.st_dev, current.st_ino) != (named.st_dev, named.st_ino)
        ):
            raise AdmissionDenied("UNTRUSTED_ADMISSION_INODE")
        return fd
    except BaseException:
        os.close(fd)
        raise


def read_private(path: Path, mode: int) -> bytes:
    """Read bounded trusted file content through its checked descriptor."""
    with os.fdopen(trusted_fd(path, mode), "rb") as stream:
        payload = stream.read(8193)
    if len(payload) > 8192:
        raise AdmissionDenied("ADMISSION_FILE_TOO_LARGE")
    return payload


def check_permit(path: Path) -> str:
    """Require a non-expired short-lived permit for a gated-only image."""
    try:
        permit = json.loads(read_private(path, 0o600))
    except (ValueError, UnicodeError) as exc:
        raise AdmissionDenied("PERMIT_INVALID_JSON") from exc
    if not isinstance(permit, dict):
        raise AdmissionDenied("PERMIT_INVALID_FORMAT")
    digest = permit.get("signed_oci_ref")
    if not isinstance(digest, str) or not IMAGE.fullmatch(digest):
        raise AdmissionDenied("PERMIT_MISSING_PINNED_IMAGE")
    if (
        permit.get("schema") != 1
        or permit.get("mode") != "gated-only"
        or permit.get("writer_protocol") != "G_SH_B_SH_W_EX"
    ):
        raise AdmissionDenied("PERMIT_NOT_GATED_ONLY")
    expiration = permit.get("expires_unix")
    now = time.time()
    if type(expiration) not in (float, int):
        raise AdmissionDenied("PERMIT_EXPIRATION_INVALID")
    if not now < expiration <= now + 300:
        raise AdmissionDenied("PERMIT_EXPIRED_OR_TOO_LONG")
    return digest


def check_override(path: Path, image: str) -> None:
    """Require one exact pinned image in private root-owned YAML override."""
    trusted_dir(path.parent)
    body = read_private(path, 0o600).decode("utf-8")
    images = COMPOSE_IMAGE.findall(body)
    if len(images) != 1 or images[0] != image:
        raise AdmissionDenied("OVERRIDE_IMAGE_NOT_EXACTLY_PINNED")


def check_gate(gate: Path, override: Path) -> None:
    """Ensure a maintenance G EX cannot overlap this launch admission."""
    if os.geteuid() != 0:
        raise AdmissionDenied("ADMISSION_ROOT_REQUIRED")
    trusted_dir(gate)
    for name in ("B", "W"):
        with os.fdopen(trusted_fd(gate / name, 0o640), "rb"):
            pass
    with os.fdopen(trusted_fd(gate / "G", 0o640), "rb") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise AdmissionDenied("MAINTENANCE_G_EX_ACTIVE") from exc
        image = check_permit(gate / "compose-permit.json")
        check_override(override, image)


def main() -> int:
    """Default inert; checked permit is not release or signature approval."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--gate-dir", type=Path)
    parser.add_argument("--pinned-override", type=Path)
    args = parser.parse_args()
    if not args.check:
        print("DRY_RUN: no owner gate was inspected")
        return 0
    if args.gate_dir is None or args.pinned_override is None:
        print("BLOCKED: exact gate and pinned override required")
        return 2
    try:
        check_gate(args.gate_dir, args.pinned_override)
    except (AdmissionDenied, OSError, UnicodeError) as exc:
        print("COMPOSE_ADMISSION_DENIED", type(exc).__name__, str(exc)[:100])
        return 3
    print("GATED_ENTRYPOINT_PERMIT_OK; NOT_RELEASE_OR_SIGNATURE_APPROVAL")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
