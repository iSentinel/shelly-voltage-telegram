#!/usr/bin/env python3
"""Reproduce a candidate source tar from exact Git SHA; never publish it."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import os
import re
import socket
import stat
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

SCRIPT_DIR = Path(__file__).resolve().parent
SHA = re.compile(r"[a-f0-9]{40}\Z")
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+-hw\Z")
MAX_GIT_ARCHIVE = 64 * 1024 * 1024
MANIFEST_DIR = Path("/var/lib/svt-qa-release-manifests")


class ManifestBlocked(ValueError):
    """Untrusted source files or mutable release inputs block this audit."""


def arguments() -> argparse.Namespace:
    """Require QA-only paths and immutable candidate version/commit."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qa-only", action="store_true", required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--sha256sums", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--jsonl", action="store_true")
    return parser.parse_args()


def source_checker():
    """Use existing source checksum and tar-member safety validation."""
    file = SCRIPT_DIR / "verify-source-release-bundle.py"
    spec = importlib.util.spec_from_file_location("svt_manifest_source", file)
    if spec is None or spec.loader is None:
        raise ManifestBlocked("MANIFEST_SOURCE_CHECKER_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SOURCE = source_checker()


def git(repo: Path, *args: str, binary: bool = False):
    """Run bounded local Git commands without fetching or changing refs."""
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=not binary,
        timeout=35,
        check=False,
    )
    if result.returncode:
        raise ManifestBlocked("MANIFEST_LOCAL_GIT_FAILED")
    return result.stdout if binary else result.stdout.strip()


def validate_repo(args: argparse.Namespace) -> int:
    """Demand a clean local QA worktree on the exact commit under test."""
    repo = args.repo
    if (
        not args.qa_only
        or not SHA.fullmatch(args.commit)
        or not VERSION.fullmatch(args.version)
        or not repo.is_absolute()
        or any(part.is_symlink() for part in (repo, *repo.parents))
        or repo == Path("/opt/shelly-voltage")
        or not repo.is_dir()
    ):
        raise ManifestBlocked("MANIFEST_QA_INPUT_INVALID")
    if git(repo, "rev-parse", "--show-toplevel") != str(repo):
        raise ManifestBlocked("MANIFEST_REPO_ROOT_MISMATCH")
    if git(repo, "rev-parse", "HEAD") != args.commit:
        raise ManifestBlocked("MANIFEST_GIT_SHA_MISMATCH")
    if git(repo, "status", "--porcelain", "--untracked-files=all"):
        raise ManifestBlocked("MANIFEST_DIRTY_SOURCE")
    version = git(repo, "show", f"{args.commit}:VERSION")
    release = git(repo, "show", f"{args.commit}:RELEASE_VERSION")
    if version != args.version or release != args.version:
        raise ManifestBlocked("MANIFEST_VERSION_MISMATCH")
    epoch = git(repo, "show", "-s", "--format=%ct", args.commit)
    if not epoch.isdecimal() or int(epoch) < 1:
        raise ManifestBlocked("MANIFEST_GIT_EPOCH_INVALID")
    return int(epoch)


def verify_archive(args: argparse.Namespace) -> str:
    """Verify candidate checksum, name, private paths and tar versions."""
    archive = args.archive
    sums = args.sha256sums
    name = f"shelly-voltage-telegram-{args.version}.tar.gz"
    if (
        not archive.is_absolute()
        or archive.name != name
        or sums != archive.parent / "SHA256SUMS"
    ):
        raise ManifestBlocked("MANIFEST_ARCHIVE_PATH_MISMATCH")
    SOURCE.safe_file(archive, SOURCE.MAX_FILE_BYTES)
    SOURCE.safe_file(sums, 16384)
    check = argparse.Namespace(
        archive=archive, sha256sums=sums, version=args.version
    )
    SOURCE.verify_checksums(check)
    SOURCE.verify_archive_versions(check)
    with archive.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def safe_git_archive(data: bytes, root: Path) -> None:
    """Reject symlinks, traversal and abnormal entries before extraction."""
    if not 0 < len(data) <= MAX_GIT_ARCHIVE:
        raise ManifestBlocked("MANIFEST_GIT_ARCHIVE_OVERSIZED")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as bundle:
        for item in bundle:
            name = PurePosixPath(item.name)
            if (
                name.is_absolute()
                or ".." in name.parts
                or str(name) != item.name.rstrip("/")
                or not (item.isfile() or item.isdir())
            ):
                raise ManifestBlocked("MANIFEST_GIT_ENTRY_UNSAFE")
        bundle.extractall(path=root, filter="data")


def reproduce(args: argparse.Namespace, epoch: int, digest: str) -> None:
    """Build exact release-script tar in private temp and compare bytes."""
    data = git(args.repo, "archive", "--format=tar", args.commit, binary=True)
    with tempfile.TemporaryDirectory(prefix="svt-qa-release-") as temp:
        root = Path(temp)
        source = root / "source"
        source.mkdir(mode=0o700)
        safe_git_archive(data, source)
        result = subprocess.run(
            [
                "bash",
                str(source / "scripts/release.sh"),
                "--output-dir",
                str(root / "output"),
                "--source-date-epoch",
                str(epoch),
            ],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        if result.returncode:
            raise ManifestBlocked("MANIFEST_GIT_REBUILD_FAILED")
        rebuilt = root / "output" / args.archive.name
        with rebuilt.open("rb") as stream:
            new_digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if (
            new_digest != digest
            or rebuilt.read_bytes() != args.archive.read_bytes()
        ):
            raise ManifestBlocked("MANIFEST_EXACT_GIT_ARCHIVE_MISMATCH")


def plan(args: argparse.Namespace) -> dict:
    """Prove only local source parity, not signatures or release authority."""
    epoch = validate_repo(args)
    digest = verify_archive(args)
    reproduce(args, epoch, digest)
    return {
        "status": "PASS_QA_EXACT_SOURCE_BYTES_ONLY",
        "version": args.version,
        "source_commit": args.commit,
        "source_date_epoch": epoch,
        "source_sha256": digest,
        "git_source_reproducible": True,
        "ci_authentication_verified": False,
        "source_sigstore_verified": False,
        "oci_signature_verified": False,
        "oci_attestations_verified": False,
        "owner_approval_verified": False,
        "release_authorized": False,
        "rollback_authorized": False,
        "production_modified": False,
    }


def save_report(output: Path, report: dict) -> None:
    """Create a private one-time QA record without overwriting old proof."""
    if output.parent != MANIFEST_DIR or output.name != (
        f"source-{report['source_commit']}.json"
    ):
        raise ManifestBlocked("MANIFEST_OUTPUT_PATH_INVALID")
    info = MANIFEST_DIR.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != 0
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise ManifestBlocked("MANIFEST_OUTPUT_DIRECTORY_UNSAFE")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW
    fd = os.open(output, flags, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as stream:
        json.dump(report, stream, sort_keys=True, separators=(",", ":"))
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    parent = os.open(MANIFEST_DIR, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


def main() -> int:
    """Return explicit PASS/BLOCKED with no Docker, GitHub or secret writes."""
    args = arguments()
    try:
        if socket.gethostname() not in {"proxmox", "shelly-docker-reboot-qa"}:
            raise ManifestBlocked("MANIFEST_QA_HOST_INVALID")
        report = plan(args)
        if args.output is not None:
            save_report(args.output, report)
    except (
        OSError,
        ValueError,
        subprocess.TimeoutExpired,
        tarfile.TarError,
    ) as exc:
        report = {
            "status": "BLOCKED",
            "reason": (
                str(exc)
                if isinstance(exc, ManifestBlocked)
                else "MANIFEST_QA_EVIDENCE_UNAVAILABLE"
            ),
            "release_authorized": False,
            "rollback_authorized": False,
            "production_modified": False,
        }
    if args.jsonl:
        print(json.dumps(report, separators=(",", ":")))
    else:
        print("EXACT SOURCE PUBLICATION QA:", report["status"])
        if report["status"] == "BLOCKED":
            print("reason:", report["reason"])
    return 0 if report["status"].startswith("PASS_") else 3


if __name__ == "__main__":
    raise SystemExit(main())
