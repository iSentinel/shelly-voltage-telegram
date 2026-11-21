#!/usr/bin/env python3
"""Verify a keyless-signed source tarball before any release installation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import tarfile
from pathlib import Path, PurePosixPath

VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+-hw\Z")
DIGEST_LINE = re.compile(r"([0-9a-f]{64})  ([^/\s]+)\Z")
REQUIRED = {
    "VERSION",
    "RELEASE_VERSION",
    "app.py",
    "writer_fence.py",
    "Dockerfile",
    "compose.yaml",
    "scripts/backup.sh",
    "scripts/verify-source-release-bundle.py",
    "scripts/verify-signed-release-pair.py",
}
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = 600 * 1024 * 1024
MAX_MEMBERS = 20000
PRIVATE_PARTS = {
    ".env",
    ".work-state",
    "secrets",
    "validation",
    "dist-publish",
    "__pycache__",
    ".ruff_cache",
}
PRIVATE_FILES = {"ROADMAP.md", ".roadmap-update.lock"}
COSIGN = Path("/usr/local/bin/cosign")
ISSUER = "https://token.actions.githubusercontent.com"


class SourceBlocked(ValueError):
    """Stable fail-closed code for an untrusted source release."""


def arguments() -> argparse.Namespace:
    """Accept only explicit release asset paths with no production defaults."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--sha256sums", required=True, type=Path)
    parser.add_argument(
        "--expected-commit",
        default="",
        help="Require exact Git SHA in signed GitHub workflow certificate.",
    )
    parser.add_argument("--jsonl", action="store_true")
    return parser.parse_args()


def safe_file(path: Path, bound: int) -> None:
    """Reject linked or modified release artifacts before reading bytes."""
    if not path.is_absolute() or ".." in path.parts:
        raise SourceBlocked("SOURCE_PATH_INVALID")
    if any(parent.is_symlink() for parent in path.parents):
        raise SourceBlocked("SOURCE_PARENT_LINKED")
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid != os.geteuid()
        or info.st_mode & 0o022
        or not 0 < info.st_size <= bound
    ):
        raise SourceBlocked("SOURCE_FILE_UNSAFE")


def validate_paths(args: argparse.Namespace) -> None:
    """Require one exact immutable archive, bundle and checksum set."""
    if not VERSION.fullmatch(args.version):
        raise SourceBlocked("SOURCE_VERSION_INVALID")
    commit = getattr(args, "expected_commit", "")
    if commit and not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise SourceBlocked("SOURCE_EXPECTED_COMMIT_INVALID")
    expected = f"shelly-voltage-telegram-{args.version}.tar.gz"
    if (
        args.archive.name != expected
        or args.bundle != args.archive.with_name(expected + ".sigstore.json")
        or args.sha256sums != args.archive.parent / "SHA256SUMS"
    ):
        raise SourceBlocked("SOURCE_RELEASE_ASSET_MISMATCH")
    parent = args.archive.parent
    info = parent.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_mode & 0o022
    ):
        raise SourceBlocked("SOURCE_RELEASE_DIRECTORY_UNSAFE")
    safe_file(args.archive, MAX_FILE_BYTES)
    safe_file(args.bundle, 512 * 1024)
    safe_file(args.sha256sums, 16384)


def verify_checksums(args: argparse.Namespace) -> str:
    """Bind exactly one archive checksum to the selected filename."""
    lines = args.sha256sums.read_text(encoding="ascii").splitlines()
    matches = [
        m.group(1)
        for line in lines
        if (m := DIGEST_LINE.fullmatch(line))
        and m.group(2) == args.archive.name
    ]
    if len(matches) != 1:
        raise SourceBlocked("SOURCE_SHA256_ENTRY_INVALID")
    with args.archive.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != matches[0]:
        raise SourceBlocked("SOURCE_ARCHIVE_SHA256_MISMATCH")
    return actual


def safe_member(
    member: tarfile.TarInfo,
    root: str,
    seen: set[str],
    count: int,
    total: int,
) -> str:
    """Reject unsafe or private tar paths before inspecting their content."""
    entry = PurePosixPath(member.name)
    parts = entry.parts
    canonical = str(entry)
    if (
        not parts
        or parts[0] != root
        or member.name != canonical
        or ".." in parts
        or not (member.isfile() or member.isdir())
        or member.mode & 0o6000
        or member.size > MAX_FILE_BYTES
        or count > MAX_MEMBERS
        or total > MAX_TOTAL_BYTES
        or canonical in seen
    ):
        raise SourceBlocked("SOURCE_ARCHIVE_MEMBER_INVALID")
    seen.add(canonical)
    if (
        any(part in PRIVATE_PARTS for part in parts[1:])
        or any(part in PRIVATE_FILES for part in parts[1:])
        or parts[-1].endswith((".pem", ".key", ".secret", ".token"))
    ):
        raise SourceBlocked("SOURCE_ARCHIVE_PRIVATE_MEMBER")
    return str(PurePosixPath(*parts[1:]))


def verify_archive_versions(args: argparse.Namespace) -> None:
    """Inspect bounded source members and both immutable version markers."""
    root = f"shelly-voltage-telegram-{args.version}"
    present: set[str] = set()
    versions: dict[str, str] = {}
    seen: set[str] = set()
    size = 0
    with tarfile.open(args.archive, "r:gz") as archive:
        for number, member in enumerate(archive, 1):
            size += member.size
            relative = safe_member(member, root, seen, number, size)
            if member.isfile() and member.size > 0:
                present.add(relative)
            if relative not in {"VERSION", "RELEASE_VERSION"}:
                continue
            if not member.isfile() or not 0 < member.size <= 128:
                raise SourceBlocked("SOURCE_ARCHIVE_VERSION_UNSAFE")
            content = archive.extractfile(member)
            if content is None:
                raise SourceBlocked("SOURCE_ARCHIVE_VERSION_UNSAFE")
            versions[relative] = content.read(129).decode("ascii").strip()
    if not REQUIRED.issubset(present):
        raise SourceBlocked("SOURCE_ARCHIVE_INCOMPLETE")
    if versions != {"VERSION": args.version, "RELEASE_VERSION": args.version}:
        raise SourceBlocked("SOURCE_ARCHIVE_VERSION_MISMATCH")


def trusted_cosign() -> str:
    """Require root-owned Cosign v3 in an immutable command location."""
    for item in (COSIGN, *COSIGN.parents):
        info = item.lstat()
        valid = (
            stat.S_ISREG(info.st_mode)
            if item == COSIGN
            else stat.S_ISDIR(info.st_mode)
        )
        if not valid or info.st_uid != 0 or info.st_mode & 0o022:
            raise SourceBlocked("SOURCE_COSIGN_PATH_UNSAFE")
    if not os.access(COSIGN, os.X_OK):
        raise SourceBlocked("SOURCE_COSIGN_UNAVAILABLE")
    response = subprocess.run(
        [str(COSIGN), "version"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    match = re.search(r"(?m)^GitVersion:\s*v?([0-9]+)\.", response.stdout)
    if response.returncode or not match or int(match.group(1)) < 3:
        raise SourceBlocked("SOURCE_COSIGN_VERSION_INVALID")
    return str(COSIGN)


def verify_signature(args: argparse.Namespace) -> None:
    """Check the tagged GitHub workflow, SHA and trusted Sigstore bundle."""
    identity = (
        "https://github.com/iSentinel/shelly-voltage-telegram/"
        f".github/workflows/release.yml@refs/tags/v{args.version}"
    )
    command = [
        trusted_cosign(),
        "verify-blob",
        "--bundle",
        str(args.bundle),
        "--certificate-identity",
        identity,
        "--certificate-oidc-issuer",
        ISSUER,
        "--certificate-github-workflow-ref",
        f"refs/tags/v{args.version}",
        "--certificate-github-workflow-repository",
        "iSentinel/shelly-voltage-telegram",
    ]
    commit = getattr(args, "expected_commit", "")
    if commit:
        command.extend(["--certificate-github-workflow-sha", commit])
    command.append(str(args.archive))
    result = subprocess.run(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=90,
        check=False,
    )
    if result.returncode:
        raise SourceBlocked("SOURCE_BLOB_SIGNATURE_UNVERIFIED")


def verify(args: argparse.Namespace) -> dict:
    """Produce read-only evidence, never installation authorization."""
    validate_paths(args)
    checksum = verify_checksums(args)
    verify_archive_versions(args)
    verify_signature(args)
    if verify_checksums(args) != checksum:
        raise SourceBlocked("SOURCE_ARCHIVE_CHANGED_DURING_VERIFICATION")
    return {
        "status": "PASS_SIGNED_SOURCE_BYTES_ONLY",
        "version": args.version,
        "signed_source_verified": True,
        "source_sha256_verified": True,
        "source_sha256": checksum,
        "source_commit_bound": bool(getattr(args, "expected_commit", "")),
        "release_authorized": False,
        "production_modified": False,
    }


def main() -> int:
    """Fail closed with a stable non-secret summary and exit status."""
    args = arguments()
    try:
        report = verify(args)
    except (
        SourceBlocked,
        OSError,
        UnicodeError,
        tarfile.TarError,
        subprocess.TimeoutExpired,
    ) as exc:
        report = {
            "status": "BLOCKED",
            "reason": str(exc)
            if isinstance(exc, SourceBlocked)
            else "SOURCE_VERIFICATION_UNAVAILABLE",
            "release_authorized": False,
            "production_modified": False,
        }
    if args.jsonl:
        print(json.dumps(report, separators=(",", ":")))
    else:
        print("SIGNED SOURCE:", report["status"])
        if report["status"] == "BLOCKED":
            print("reason:", report["reason"])
    return 0 if report["status"].startswith("PASS_") else 3


if __name__ == "__main__":
    raise SystemExit(main())
