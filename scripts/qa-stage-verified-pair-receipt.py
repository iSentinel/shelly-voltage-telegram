#!/usr/bin/env python3
"""Stage an immutable signed source/OCI receipt on disposable QA only."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import socket
import stat
import sys
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from writer_fence import (  # noqa: E402
    quiescent_writer_lease,
    update_gate,
)

STAGE_ROOT = Path("/var/lib/svt-qa-verified-pair-20261119")
MAX_ARCHIVE = 512 * 1024 * 1024


class StageBlocked(ValueError):
    """An unsigned, incomplete or unsafe release cannot be staged."""


def pair_verifier():
    """Import the sibling read-only verifier without user Python paths."""
    script = Path(__file__).with_name("verify-signed-release-pair.py")
    spec = importlib.util.spec_from_file_location("svt_qa_pair_stage", script)
    if spec is None or spec.loader is None:
        raise StageBlocked("STAGE_PAIR_VERIFIER_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PAIR = pair_verifier()
TOKEN = re.compile(r"qa-[a-zA-Z0-9_.-]{1,90}\Z")


def arguments() -> argparse.Namespace:
    """Require an explicit QA-only stage or a non-mutating dry run."""
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--qa-stage", action="store_true")
    action.add_argument("--dry-run", action="store_true")
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--digest", required=True)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--sha256sums", required=True, type=Path)
    parser.add_argument("--container")
    parser.add_argument("--database", type=Path)
    parser.add_argument("--fence-dir", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--token", default="")
    parser.add_argument("--jsonl", action="store_true")
    return parser.parse_args()


def require_isolated_root() -> None:
    """Never write on production, or on an unexpected QA host."""
    if os.geteuid() != 0 or socket.gethostname() != "shelly-docker-reboot-qa":
        raise StageBlocked("STAGE_DISPOSABLE_LXC114_ONLY")
    parent = STAGE_ROOT.parent.lstat()
    if (
        not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != 0
        or parent.st_mode & 0o022
    ):
        raise StageBlocked("STAGE_PARENT_UNSAFE")
    if STAGE_ROOT.exists() or STAGE_ROOT.is_symlink():
        raise StageBlocked("STAGE_EXISTING_OUTPUT_REFUSED")


def qa_rescue_module():
    """Load the pinned WAL/PHOTO checker, never a mutable caller import."""
    file = Path(__file__).with_name("stage-guarded-update.py")
    spec = importlib.util.spec_from_file_location("svt_joined_qa_rescue", file)
    if spec is None or spec.loader is None:
        raise StageBlocked("STAGE_RESCUE_VERIFIER_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_qa_rescue(args: argparse.Namespace) -> None:
    """Require same-token pending and complete newest WAL/PHOTO under QA."""
    if not TOKEN.fullmatch(args.token):
        raise StageBlocked("STAGE_TRANSACTION_TOKEN_INVALID")
    for key in ("container", "database", "fence_dir", "output"):
        if getattr(args, key, None) is None:
            raise StageBlocked("STAGE_QA_RESCUE_ARGUMENT_MISSING")
    module = qa_rescue_module()
    args.qa_only = True
    module.check_paths(args)
    info = module.inspect_container(args.container)
    module.check_container(info, args)
    module.inspect_stage(args, info)


def check_locked_pending(args: argparse.Namespace) -> None:
    """Read the exact root marker while the common transaction lock is held."""
    path = args.fence_dir / ".svt-update-pending"
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != 0
            or info.st_nlink != 1
            or not 0 < info.st_size <= 128
        ):
            raise StageBlocked("STAGE_PENDING_MARKER_UNSAFE")
        if os.read(fd, 129).decode("ascii").strip() != args.token:
            raise StageBlocked("STAGE_PENDING_TOKEN_CHANGED")
    finally:
        os.close(fd)


@contextmanager
def sealed_pending_receipt(args: argparse.Namespace):
    """Hold owner and update locks through receipt commit, including fsync."""
    if not TOKEN.fullmatch(args.token):
        raise StageBlocked("STAGE_TRANSACTION_TOKEN_INVALID")
    previous = os.environ.get("SVT_WRITER_FENCE_DIR")
    os.environ["SVT_WRITER_FENCE_DIR"] = str(args.fence_dir)
    try:
        with (
            quiescent_writer_lease(args.database),
            update_gate(args.database),
        ):
            check_locked_pending(args)
            checker = qa_rescue_module()
            if checker.database_digest(args.database) != (
                checker.database_digest(args.output)
            ):
                raise StageBlocked("STAGE_RESCUE_HISTORY_CHANGED")
            yield
    finally:
        if previous is None:
            os.environ.pop("SVT_WRITER_FENCE_DIR", None)
        else:
            os.environ["SVT_WRITER_FENCE_DIR"] = previous


def copy_private(source: Path, target: Path, size_limit: int) -> None:
    """Write one exclusive root-private artifact and sync its file bytes."""
    info = source.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or not 0 < info.st_size <= size_limit
    ):
        raise StageBlocked("STAGE_INPUT_UNSAFE")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    fd = os.open(target, flags, 0o600)
    with os.fdopen(fd, "wb") as output, source.open("rb") as input_file:
        shutil.copyfileobj(input_file, output, 1024 * 1024)
        output.flush()
        os.fsync(output.fileno())


def sync_directory(directory: Path) -> None:
    """Persist directory entries before reporting a staged receipt."""
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def staged_arguments(
    args: argparse.Namespace, stage: Path
) -> argparse.Namespace:
    """Verify the private copied bytes instead of mutable input files."""
    archive = stage / args.archive.name
    return argparse.Namespace(
        version=args.version,
        commit=args.commit,
        digest=args.digest,
        archive=archive,
        bundle=stage / (args.archive.name + ".sigstore.json"),
        sha256sums=stage / "SHA256SUMS",
    )


def record_for_stage(args: argparse.Namespace, evidence: dict) -> dict:
    """Build one exact paired receipt from completed verification only."""
    if (
        evidence.get("status") != "PASS_SIGNED_SOURCE_OCI_PAIR_ONLY"
        or evidence.get("version") != args.version
        or evidence.get("commit") != args.commit
        or evidence.get("digest") != args.digest
        or evidence.get("digest_suffix") != args.digest[-12:]
        or evidence.get("release_authorized") is not False
        or evidence.get("rollback_authorized") is not False
    ):
        raise StageBlocked("STAGE_PAIR_EVIDENCE_INVALID")
    for key in (
        "source_signature_verified",
        "oci_signature_verified",
        "oci_attestations_verified",
    ):
        if evidence.get(key) is not True:
            raise StageBlocked("STAGE_PAIR_EVIDENCE_INVALID")
    with args.archive.open("rb") as stream:
        source_sha = hashlib.file_digest(stream, "sha256").hexdigest()
    if evidence.get("source_sha256") != source_sha:
        raise StageBlocked("STAGE_SOURCE_SHA256_MISMATCH")
    return {
        "version": args.version,
        "digest": args.digest,
        "source_commit": args.commit,
        "source_sha256": source_sha,
        "transaction_token": args.token,
        "pair_status": evidence["status"],
        "source_signature_verified": True,
        "oci_signature_verified": True,
        "oci_attestations_verified": True,
    }


def publish_private_receipt(stage: Path, record: dict) -> None:
    """Commit a mode0600 receipt last; never overwrite a prior receipt."""
    if (stage / "deployed.json").exists():
        raise StageBlocked("STAGE_RECEIPT_EXISTS")
    tmp = stage / ".receipt-pending"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    fd = os.open(tmp, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        json.dump(record, output, sort_keys=True, separators=(",", ":"))
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    os.link(tmp, stage / "deployed.json")
    tmp.unlink()
    sync_directory(stage)


def stage_release(args: argparse.Namespace, directory: Path) -> dict:
    """Keep a private, fsynced pair; publish receipt only after real proof."""
    name = f"shelly-voltage-telegram-{args.version}.tar.gz"
    if args.archive.name != name:
        raise StageBlocked("STAGE_ARCHIVE_NAME_INVALID")
    if directory.exists() or directory.is_symlink():
        raise StageBlocked("STAGE_EXISTING_OUTPUT_REFUSED")
    directory.mkdir(mode=0o700)
    copy_private(args.archive, directory / name, MAX_ARCHIVE)
    copy_private(
        args.bundle, directory / (name + ".sigstore.json"), 512 * 1024
    )
    copy_private(args.sha256sums, directory / "SHA256SUMS", 16384)
    sync_directory(directory)
    copied = staged_arguments(args, directory)
    copied.token = args.token
    evidence = PAIR.verify(copied)
    verify_qa_rescue(args)
    record = record_for_stage(copied, evidence)
    with sealed_pending_receipt(args):
        publish_private_receipt(directory, record)
    return {
        "status": "PASS_QA_PAIR_RECEIPT_STAGED",
        "release_authorized": False,
        "production_modified": False,
        "version": args.version,
    }


def main() -> int:
    """Retain incomplete QA files for diagnosis, never touch production."""
    args = arguments()
    try:
        PAIR.verify_inputs(args)
        if args.qa_stage:
            require_isolated_root()
            verify_qa_rescue(args)
            report = stage_release(args, STAGE_ROOT)
        else:
            report = PAIR.verify(args)
    except (ValueError, OSError, TypeError) as error:
        report = {
            "status": "BLOCKED",
            "reason": str(error)
            if isinstance(error, StageBlocked)
            else "STAGE_PAIR_VERIFICATION_FAILED",
            "release_authorized": False,
            "production_modified": False,
        }
    if args.jsonl:
        print(json.dumps(report, separators=(",", ":")))
    else:
        print("QA PAIR STAGE:", report["status"])
    return 0 if report["status"].startswith("PASS_") else 3


if __name__ == "__main__":
    sys.exit(main())
