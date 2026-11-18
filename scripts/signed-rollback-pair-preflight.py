#!/usr/bin/env python3
"""Read-only matching of source and signed-updater rollback snapshots."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import tarfile
from pathlib import Path

VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+-hw")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
IMAGE = "ghcr.io/isentinel/shelly-voltage-telegram@"


class Blocked(ValueError):
    """Stable non-secret reason code for untrusted snapshot data."""


def private_directory(path: Path) -> None:
    """Only inspect root-owned 0700 directories without symbolic links."""

    if not path.is_absolute() or any(
        p.is_symlink() for p in (path, *path.parents)
    ):
        raise Blocked("SNAPSHOT_PATH_LINKED_OR_RELATIVE")
    try:
        info = path.stat()
    except OSError as exc:
        raise Blocked("SNAPSHOT_DIRECTORY_UNAVAILABLE") from exc
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
        raise Blocked("SNAPSHOT_DIRECTORY_UNSAFE")
    if stat.S_IMODE(info.st_mode) != 0o700:
        raise Blocked("SNAPSHOT_DIRECTORY_UNSAFE")


def safe_file(path: Path, private: bool = False) -> None:
    """Refuse linked, nonregular, multiply linked or exposed private data."""

    if path.is_symlink():
        raise Blocked("SNAPSHOT_FILE_LINKED")
    try:
        info = path.stat()
    except OSError as exc:
        raise Blocked("SNAPSHOT_FILE_MISSING") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise Blocked("SNAPSHOT_FILE_UNSAFE")
    if private and stat.S_IMODE(info.st_mode) != 0o600:
        raise Blocked("SIGNED_RECEIPT_PERMISSIONS_UNSAFE")


def read_small(path: Path, private: bool = False) -> str:
    """Bound metadata before reading, to avoid accepting oversized files."""

    safe_file(path, private)
    if not 0 < path.stat().st_size <= 4096:
        raise Blocked("SNAPSHOT_METADATA_SIZE")
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise Blocked("SNAPSHOT_METADATA_INVALID") from exc


def checksum_matches(path: Path) -> None:
    """Verify a standalone archive against its literal SHA256 sidecar."""

    safe_file(path)
    sidecar = Path(str(path) + ".sha256")
    content = read_small(sidecar).strip()
    match = re.fullmatch(r"([0-9a-f]{64})  (.+)", content)
    if not match or match.group(2) != str(path):
        raise Blocked("SNAPSHOT_CHECKSUM_MANIFEST_INVALID")
    with path.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != match.group(1):
        raise Blocked("SNAPSHOT_CHECKSUM_MISMATCH")


def source_versions(archive: Path) -> str:
    """Inspect version markers in tar members without extracting sources."""

    versions: dict[str, str] = {}
    try:
        with tarfile.open(archive, "r:gz") as bundle:
            for entry in bundle:
                if entry.name not in ("./VERSION", "./RELEASE_VERSION"):
                    continue
                if entry.name in versions or not entry.isfile():
                    raise Blocked("SOURCE_VERSION_INVALID")
                if not 1 <= entry.size <= 128:
                    raise Blocked("SOURCE_VERSION_INVALID")
                stream = bundle.extractfile(entry)
                if stream is None:
                    raise Blocked("SOURCE_VERSION_INVALID")
                versions[entry.name] = stream.read(129).decode("ascii").strip()
    except (OSError, UnicodeError, tarfile.TarError) as exc:
        raise Blocked("SOURCE_ARCHIVE_INVALID") from exc
    if set(versions) != {"./VERSION", "./RELEASE_VERSION"}:
        raise Blocked("SOURCE_VERSION_MISSING")
    old = versions["./VERSION"]
    if not VERSION.fullmatch(old) or versions["./RELEASE_VERSION"] != old:
        raise Blocked("SOURCE_VERSION_MISMATCH")
    return old


def upgrade_versions(snapshot: Path) -> tuple[str, str]:
    """Require exactly two consistent version keys in the source manifest."""

    lines = read_small(snapshot / "manifest.env").splitlines()
    values = {}
    for line in lines:
        if "=" not in line:
            raise Blocked("UPGRADE_MANIFEST_INVALID")
        key, value = line.split("=", 1)
        if key in values or key not in ("from_version", "to_version"):
            raise Blocked("UPGRADE_MANIFEST_INVALID")
        if not VERSION.fullmatch(value):
            raise Blocked("UPGRADE_MANIFEST_INVALID")
        values[key] = value
    if set(values) != {"from_version", "to_version"}:
        raise Blocked("UPGRADE_MANIFEST_INVALID")
    return values["from_version"], values["to_version"]


def receipt_identity(snapshot: Path) -> tuple[str, str]:
    """Verify the old protected release record and pinned Compose image."""

    try:
        record = json.loads(read_small(snapshot / "deployed.json", True))
    except Blocked:
        raise
    except (ValueError, TypeError) as exc:
        raise Blocked("OLD_RECEIPT_INVALID") from exc
    if not isinstance(record, dict):
        raise Blocked("OLD_RECEIPT_INVALID")
    old, digest = record.get("version"), record.get("digest")
    if not isinstance(old, str) or not VERSION.fullmatch(old):
        raise Blocked("OLD_VERSION_INVALID")
    if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
        raise Blocked("OLD_DIGEST_INVALID")
    override = read_small(snapshot / "override.yaml", True)
    images = re.findall(r"(?m)^\s*image:\s*(\S+)\s*$", override)
    if images != [IMAGE + digest]:
        raise Blocked("OLD_OVERRIDE_DIGEST_MISMATCH")
    return old, digest


def verify_old_signature(version: str, digest: str) -> bool:
    """Verify the old immutable image against the trusted release workflow."""

    identity = (
        "https://github.com/iSentinel/shelly-voltage-telegram/"
        ".github/workflows/image.yml@refs/tags/v" + version
    )
    command = [
        "/usr/local/bin/cosign",
        "verify",
        "--certificate-identity",
        identity,
        "--certificate-oidc-issuer",
        "https://token.actions.githubusercontent.com",
        IMAGE + digest,
    ]
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=70,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Blocked("OLD_IMAGE_SIGNATURE_UNAVAILABLE") from exc
    if result.returncode:
        raise Blocked("OLD_IMAGE_SIGNATURE_UNVERIFIED")
    return True


def check_sqlite_archives(source: Path, signed: Path) -> None:
    """Require exactly one checksummed SQLite archive from each snapshot."""

    for directory in (source, signed):
        archives = list(directory.glob("voltage-*.sqlite3.gz"))
        if len(archives) != 1:
            raise Blocked("SQLITE_ARCHIVE_COUNT_INVALID")
        checksum_matches(archives[0])


def check_pair(
    source: Path, signed: Path, verify_signature: bool = False
) -> dict:
    """Check a version-paired old source and OCI digest without restoring."""

    private_directory(source)
    private_directory(signed)
    old, target = upgrade_versions(source)
    archive = source / "source.tar.gz"
    checksum_matches(archive)
    if source_versions(archive) != old:
        raise Blocked("SOURCE_MANIFEST_VERSION_MISMATCH")
    receipt_version, digest = receipt_identity(signed)
    if receipt_version != old:
        raise Blocked("SOURCE_SIGNED_VERSION_MISMATCH")
    if not re.fullmatch(
        r"[0-9]{8}T[0-9]{6}Z-" + re.escape(target), signed.name
    ):
        raise Blocked("SIGNED_SNAPSHOT_TARGET_MISMATCH")
    check_sqlite_archives(source, signed)
    signature_verified = (
        verify_old_signature(old, digest) if verify_signature else False
    )
    return {
        "status": (
            "PASS_PAIR_SIGNED_IDENTITY_ONLY"
            if signature_verified
            else "PASS_PAIR_IDENTITY_ONLY"
        ),
        "source_version": old,
        "target_version": target,
        "old_digest_suffix": digest[-12:],
        "archives_sha256_checked": True,
        "signature_verified": signature_verified,
        "sqlite_restore_verified": False,
        "production_modified": False,
        "release_authorized": False,
        "rollback_authorized": False,
    }


def main() -> int:
    """Run a read-only preflight; return 0 for identity and 2 for blocked."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-snapshot", required=True, type=Path)
    parser.add_argument("--signed-snapshot", required=True, type=Path)
    parser.add_argument(
        "--verify-signature",
        action="store_true",
        help="Read-only Cosign check against the pinned old OCI digest.",
    )
    parser.add_argument("--jsonl", action="store_true")
    args = parser.parse_args()
    try:
        report = check_pair(
            args.source_snapshot,
            args.signed_snapshot,
            verify_signature=args.verify_signature,
        )
    except (Blocked, OSError) as exc:
        report = {
            "status": "BLOCKED",
            "reason": str(exc)
            if isinstance(exc, Blocked)
            else "IO_UNAVAILABLE",
            "production_modified": False,
            "release_authorized": False,
            "rollback_authorized": False,
        }
    if args.jsonl:
        print(json.dumps(report, separators=(",", ":")))
    else:
        print("SIGNED ROLLBACK PAIR PREFLIGHT:", report["status"])
        if report["status"] == "BLOCKED":
            print("reason:", report["reason"])
    return 0 if report["status"].startswith("PASS_PAIR_") else 2


if __name__ == "__main__":
    raise SystemExit(main())
