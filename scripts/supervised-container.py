#!/usr/bin/env python3
"""Supervise one already-approved Docker container without an update bypass."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from writer_fence import (  # noqa: E402
    WriterFenceError,
    fence_directory,
    update_gate,
)

IMAGE = "ghcr.io/isentinel/shelly-voltage-telegram@"
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+-hw\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
TRANSACTION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{5,95}\Z")
STOP = False


class BootBlocked(ValueError):
    """Refuse startup unless exactly one owner and correct image are proven."""


def args_parser() -> argparse.Namespace:
    """Parse approved container and fence paths."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", required=True)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--fence-dir", required=True, type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--source-version", type=Path)
    parser.add_argument("--require-pair-receipt", action="store_true")
    parser.add_argument("--source-archive-dir", type=Path)
    parser.add_argument("--qa-offline", action="store_true")
    parser.add_argument("--once", action="store_true")
    return parser.parse_args()


def docker(*argv: str, timeout: int = 15) -> subprocess.CompletedProcess:
    """Run a bounded Docker query without shell interpretation."""
    return subprocess.run(
        ("docker", *argv),
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
    )


def inspect(name: str) -> dict:
    """Read exactly one existing Docker container's configuration."""
    result = docker("inspect", name)
    if result.returncode:
        raise BootBlocked("SVT_CONTAINER_UNAVAILABLE")
    try:
        items = json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        raise BootBlocked("SVT_DOCKER_INSPECT_INVALID") from exc
    if not isinstance(items, list) or len(items) != 1:
        raise BootBlocked("SVT_DOCKER_INSPECT_INVALID")
    return items[0]


def source_version(path: Path | None) -> str:
    """Read a root-controlled source version without following symlinks."""
    if path is None or not path.is_absolute():
        raise BootBlocked("SVT_SOURCE_VERSION_PATH_INVALID")
    for part in (path, *path.parents):
        info = part.lstat()
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise BootBlocked("SVT_SOURCE_VERSION_PATH_UNSAFE")
        if part == path:
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise BootBlocked("SVT_SOURCE_VERSION_PATH_UNSAFE")
            if not 0 < info.st_size <= 128:
                raise BootBlocked("SVT_SOURCE_VERSION_INVALID")
        elif not stat.S_ISDIR(info.st_mode):
            raise BootBlocked("SVT_SOURCE_VERSION_PATH_UNSAFE")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as source:
        version = source.read(129).decode("ascii").strip()
    if not VERSION.fullmatch(version):
        raise BootBlocked("SVT_SOURCE_VERSION_INVALID")
    return version


def require_version_pair(record: dict, version: str) -> None:
    """Require the receipt version to match the installed source."""
    receipt_version = record.get("version")
    if not isinstance(receipt_version, str) or not VERSION.fullmatch(
        receipt_version
    ):
        raise BootBlocked("SVT_RECEIPT_VERSION_INVALID")
    if receipt_version != version:
        raise BootBlocked("SVT_SOURCE_SIGNED_VERSION_MISMATCH")


def require_root_ancestors(path: Path) -> None:
    """Reject non-root-controlled parents and symbolic directory links."""
    if not isinstance(path, Path) or not path.is_absolute():
        raise BootBlocked("SVT_SIGNED_RECEIPT_PATH_INVALID")
    for parent in path.parents:
        info = parent.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != 0
            or info.st_mode & 0o022
        ):
            raise BootBlocked("SVT_SIGNED_RECEIPT_PARENT_UNSAFE")


def verify_paired_source_archive(record: dict, directory: Path) -> None:
    """Bind the private original source bytes to the trusted pair receipt."""
    if not isinstance(directory, Path) or not directory.is_absolute():
        raise BootBlocked("SVT_PAIRED_SOURCE_DIR_INVALID")
    require_root_ancestors(directory)
    info = directory.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != 0
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise BootBlocked("SVT_PAIRED_SOURCE_DIR_UNSAFE")
    name = f"shelly-voltage-telegram-{record['version']}.tar.gz"
    path = directory / name
    file_info = path.lstat()
    if (
        not stat.S_ISREG(file_info.st_mode)
        or file_info.st_uid != 0
        or file_info.st_nlink != 1
        or stat.S_IMODE(file_info.st_mode) != 0o600
        or not 0 < file_info.st_size <= 512 * 1024 * 1024
    ):
        raise BootBlocked("SVT_PAIRED_SOURCE_ARCHIVE_UNSAFE")
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != record["source_sha256"]:
        raise BootBlocked("SVT_PAIRED_SOURCE_ARCHIVE_MISMATCH")


def require_pair_proof(record: dict, image_revision: object) -> None:
    """Match a root-owned paired proof with the signed OCI source revision."""
    commit = record.get("source_commit")
    source_sha = record.get("source_sha256")
    if not isinstance(commit, str) or not COMMIT.fullmatch(commit):
        raise BootBlocked("SVT_PAIRED_COMMIT_INVALID")
    if not isinstance(source_sha, str) or not SHA256.fullmatch(source_sha):
        raise BootBlocked("SVT_PAIRED_SOURCE_SHA256_INVALID")
    transaction = record.get("transaction_token")
    if not isinstance(transaction, str) or not TRANSACTION.fullmatch(
        transaction
    ):
        raise BootBlocked("SVT_PAIRED_TRANSACTION_MISSING")
    if image_revision != commit:
        raise BootBlocked("SVT_PAIRED_OCI_REVISION_MISMATCH")
    if record.get("pair_status") != "PASS_SIGNED_SOURCE_OCI_PAIR_ONLY":
        raise BootBlocked("SVT_PAIRED_PROOF_MISSING")
    for key in (
        "source_signature_verified",
        "oci_signature_verified",
        "oci_attestations_verified",
    ):
        if record.get(key) is not True:
            raise BootBlocked("SVT_PAIRED_PROOF_INCOMPLETE")


def verify_receipt(
    path: Path | None,
    source: Path | None,
    require_pair: bool = False,
    image_revision: object = None,
    source_archive_dir: Path | None = None,
) -> str:
    """Require a private, single-linked signed release identity file."""
    if path is None:
        raise BootBlocked("SVT_SIGNED_RECEIPT_MISSING")
    require_root_ancestors(path)
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o600
        or not 0 < info.st_size <= 4096
    ):
        raise BootBlocked("SVT_SIGNED_RECEIPT_UNSAFE")
    record = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(record, dict):
        raise BootBlocked("SVT_SIGNED_RECEIPT_INVALID")
    require_version_pair(record, source_version(source))
    digest = record.get("digest", "")
    if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
        raise BootBlocked("SVT_SIGNED_RECEIPT_INVALID")
    if require_pair:
        require_pair_proof(record, image_revision)
        if source_archive_dir is None:
            raise BootBlocked("SVT_PAIRED_SOURCE_DIR_MISSING")
        verify_paired_source_archive(record, source_archive_dir)
    return IMAGE + digest


def image_revision(info: dict) -> object:
    """Read the immutable Docker revision label without assuming its type."""
    config = info.get("Config")
    if not isinstance(config, dict):
        return None
    labels = config.get("Labels")
    if not isinstance(labels, dict):
        return None
    return labels.get("org.opencontainers.image.revision")


def validate_container(info: dict, args: argparse.Namespace) -> None:
    """Refuse a foreign running writer and independent Docker restarts."""
    if info.get("State", {}).get("Running") is not False:
        raise BootBlocked("SVT_RUNNING_WRITER_OWNER_UNVERIFIED")
    policy = info.get("HostConfig", {}).get("RestartPolicy", {}).get("Name")
    if policy != "no":
        raise BootBlocked("SVT_DOCKER_RESTART_OWNER_UNFENCED")
    mounts = info.get("Mounts", [])
    shared = [
        mount
        for mount in mounts
        if mount.get("Source") == str(args.fence_dir)
        and mount.get("Destination") == "/run/svt-fence"
        and mount.get("RW") is True
    ]
    if len(shared) != 1:
        raise BootBlocked("SVT_FENCE_MOUNT_MISMATCH")
    if args.qa_offline:
        if not args.container.startswith("svt-qa-"):
            raise BootBlocked("SVT_QA_CONTAINER_UNSAFE")
        if info["HostConfig"].get("NetworkMode") != "none":
            raise BootBlocked("SVT_QA_NETWORK_UNSAFE")
    elif info.get("Config", {}).get("Image") != verify_receipt(
        args.receipt,
        args.source_version,
        getattr(args, "require_pair_receipt", False),
        image_revision(info),
        getattr(args, "source_archive_dir", None),
    ):
        raise BootBlocked("SVT_SIGNED_IMAGE_RECEIPT_MISMATCH")


def pending(database: Path) -> bool:
    """Reject unsafe marker types while holding the shared update gate."""
    with update_gate(database):
        marker = fence_directory(database) / ".svt-update-pending"
        try:
            info = marker.lstat()
        except FileNotFoundError:
            return False
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != 0
        ):
            raise BootBlocked("SVT_PENDING_MARKER_UNSAFE")
        return True


def wait_container(name: str) -> int:
    """Wait for exit, but close the Docker wait child on a stop signal."""
    child = subprocess.Popen(
        ("docker", "wait", name),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        while child.poll() is None and not STOP:
            time.sleep(0.2)
        if STOP and child.poll() is None:
            child.terminate()
        return child.wait(timeout=5)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def supervise(name: str, database: Path, once: bool) -> int:
    """Restart only after a shared-gate check; stop on any pending update."""
    attempts = 0
    while not STOP:
        if pending(database):
            print("SKIP_PENDING")
            return 0
        started = docker("start", name)
        if started.returncode:
            raise BootBlocked("SVT_DOCKER_START_FAILED")
        print("SUPERVISED_START_PASS", flush=True)
        if once:
            return 0
        result = wait_container(name)
        if STOP:
            return 0
        attempts += 1
        if attempts > 5:
            raise BootBlocked("SVT_RESTART_BUDGET_EXHAUSTED")
        print("SUPERVISED_CONTAINER_EXIT", result, flush=True)
        time.sleep(2)
    return 0


def on_signal(_signum: int, _frame: object) -> None:
    """Prevent launching another writer after supervisor termination."""
    global STOP
    STOP = True


def main() -> int:
    """Validate the sole-owner policy before starting any container."""
    args = args_parser()
    try:
        if os.geteuid() != 0 or not args.database.is_absolute():
            raise BootBlocked("SVT_SUPERVISOR_ROOT_REQUIRED")
        if not args.fence_dir.is_absolute():
            raise BootBlocked("SVT_FENCE_DIR_INVALID")
        os.environ["SVT_WRITER_FENCE_DIR"] = str(args.fence_dir)
        fence_directory(args.database)
        validate_container(inspect(args.container), args)
        signal.signal(signal.SIGTERM, on_signal)
        signal.signal(signal.SIGINT, on_signal)
        return supervise(args.container, args.database, args.once)
    except (BootBlocked, WriterFenceError, OSError, ValueError) as exc:
        code = (
            str(exc)
            if isinstance(exc, (ValueError, WriterFenceError))
            else type(exc).__name__
        )
        print(f"FAIL: {code}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
