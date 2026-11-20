#!/usr/bin/env python3
"""Join QA old/new release identity and newest WAL/PHOTO pending evidence."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tarfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
SOURCE_STAGE = Path("/var/lib/svt-qa-verified-pair-20261119")
OLD_SOURCE_STAGE = Path("/var/lib/svt-qa-old-source-20261120")


class CutoverBlocked(ValueError):
    """Refuse an incomplete or unbound QA cutover plan."""


def helper(filename: str, name: str):
    """Import only a source-pinned helper, not an installed package."""
    path = SCRIPTS / filename
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise CutoverBlocked("QA_CUTOVER_HELPER_MISSING")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BASE = helper("qa-paired-rescue-preflight.py", "svt_cutover_base")
OWNER = BASE.OWNER
STAGE = BASE.STAGE
OLD = BASE.PAIR
SOURCE_PAIR = helper(
    "verify-signed-release-pair.py", "svt_cutover_signed_source_pair"
)


def arguments() -> argparse.Namespace:
    """Require exact isolated old/new evidence; perform no installation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qa-only", action="store_true", required=True)
    parser.add_argument("--source-snapshot", required=True, type=Path)
    parser.add_argument("--signed-snapshot", required=True, type=Path)
    parser.add_argument("--new-receipt", required=True, type=Path)
    parser.add_argument("--new-version", required=True, type=Path)
    parser.add_argument("--source-archive-dir", required=True, type=Path)
    parser.add_argument(
        "--old-source-archive-dir",
        type=Path,
        help="Protected old source Sigstore assets, required for signed QA.",
    )
    parser.add_argument("--target-commit", required=True)
    parser.add_argument(
        "--verify-signatures",
        action="store_true",
        help="Explicitly verify both old/new OCI signatures in isolated QA.",
    )
    parser.add_argument(
        "--old-commit",
        default="",
        help="Exact old Git SHA required with --verify-signatures.",
    )
    parser.add_argument("--container", required=True)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--fence-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--token", required=True)
    parser.add_argument("--jsonl", action="store_true")
    return parser.parse_args()


def check_qa_boundary(args: argparse.Namespace) -> None:
    """Refuse any production endpoint or misplaced paired source receipt."""
    if (
        os.geteuid() != 0
        or socket.gethostname() != "shelly-docker-reboot-qa"
        or not args.qa_only
    ):
        raise CutoverBlocked("QA_CUTOVER_DISPOSABLE_ONLY")
    if (
        args.source_archive_dir != SOURCE_STAGE
        or args.new_receipt != SOURCE_STAGE / "deployed.json"
    ):
        raise CutoverBlocked("QA_CUTOVER_SOURCE_RECEIPT_PATH_INVALID")
    if not OWNER.COMMIT.fullmatch(args.target_commit):
        raise CutoverBlocked("QA_CUTOVER_TARGET_COMMIT_INVALID")
    signed = getattr(args, "verify_signatures", False)
    old_commit = getattr(args, "old_commit", "")
    if signed and not OWNER.COMMIT.fullmatch(old_commit):
        raise CutoverBlocked("QA_CUTOVER_OLD_COMMIT_REQUIRED")
    if not signed and old_commit:
        raise CutoverBlocked("QA_CUTOVER_SIGNATURES_NOT_REQUESTED")
    old_directory = getattr(args, "old_source_archive_dir", None)
    if signed and old_directory != OLD_SOURCE_STAGE:
        raise CutoverBlocked("QA_CUTOVER_OLD_SOURCE_STAGE_REQUIRED")
    if not signed and old_directory is not None:
        raise CutoverBlocked("QA_CUTOVER_OLD_SOURCE_UNUSED")


def check_target(args: argparse.Namespace, baseline: dict) -> dict:
    """Compare a root-private paired receipt with the actual old source."""
    record = STAGE.read_private_receipt(args.new_receipt)
    if record.get("transaction_token") != args.token:
        raise CutoverBlocked("QA_CUTOVER_TRANSACTION_TOKEN_MISMATCH")
    image = OWNER.verify_receipt(
        args.new_receipt,
        args.new_version,
        True,
        args.target_commit,
        args.source_archive_dir,
    )
    old_version, old_digest = OLD.receipt_identity(args.signed_snapshot)
    if old_version != baseline.get("old_version"):
        raise CutoverBlocked("QA_CUTOVER_OLD_VERSION_CHANGED")
    if image == OWNER.IMAGE + old_digest:
        raise CutoverBlocked("QA_CUTOVER_IMAGE_DIGEST_UNCHANGED")
    if OWNER.source_version(args.new_version) != baseline["new_version"]:
        raise CutoverBlocked("QA_CUTOVER_NEW_VERSION_DRIFT")
    return record


def check_base_proof(base: dict, signed: bool) -> None:
    """Do not confuse identity-only and exact-SHA OCI signed reports."""
    expected = (
        "PASS_QA_SIGNED_OCI_AND_WAL_ONLY"
        if signed
        else "PASS_QA_IDENTITY_AND_WAL_ONLY"
    )
    if (
        base.get("status") != expected
        or base.get("sqlite_wal_photo_parity") is not True
        or base.get("release_authorized") is not False
        or base.get("rollback_authorized") is not False
    ):
        raise CutoverBlocked("QA_CUTOVER_BASE_PROOF_INCOMPLETE")
    for key in ("old_oci_signature_verified", "new_oci_signature_verified"):
        if base.get(key) is not signed:
            raise CutoverBlocked("QA_CUTOVER_OCI_PROOF_INCOMPLETE")


def staged_pair_proof_args(
    args: argparse.Namespace, record: dict
) -> argparse.Namespace:
    """Bind actual protected source bytes to exact release identity."""
    version = record.get("version")
    digest = record.get("digest")
    commit = record.get("source_commit")
    if (
        not isinstance(version, str)
        or not OWNER.VERSION.fullmatch(version)
        or not isinstance(digest, str)
        or not OWNER.DIGEST.fullmatch(digest)
        or commit != args.target_commit
    ):
        raise CutoverBlocked("QA_CUTOVER_NEW_PAIR_IDENTITY_INVALID")
    name = f"shelly-voltage-telegram-{version}.tar.gz"
    return argparse.Namespace(
        version=version,
        commit=commit,
        digest=digest,
        archive=args.source_archive_dir / name,
        bundle=args.source_archive_dir / (name + ".sigstore.json"),
        sha256sums=args.source_archive_dir / "SHA256SUMS",
    )


def require_signed_source_pair_proof(
    proof: dict, expected: argparse.Namespace
):
    """Refuse unsigned Sigstore or mismatching multiarch OCI evidence."""
    if (
        proof.get("status") != "PASS_SIGNED_SOURCE_OCI_PAIR_ONLY"
        or proof.get("version") != expected.version
        or proof.get("commit") != expected.commit
        or proof.get("digest_suffix") != expected.digest[-12:]
        or proof.get("release_authorized") is not False
        or proof.get("rollback_authorized") is not False
        or any(
            proof.get(key) is not True
            for key in (
                "source_signature_verified",
                "oci_signature_verified",
                "oci_attestations_verified",
            )
        )
    ):
        raise CutoverBlocked("QA_CUTOVER_NEW_PAIR_PROOF_INCOMPLETE")


def reverify_signed_old_pair(args: argparse.Namespace, base: dict) -> None:
    """Require old source Sigstore + OCI at the same exact old Git SHA."""
    version, digest = OLD.receipt_identity(args.signed_snapshot)
    if version != base.get("old_version") or digest[-12:] != base.get(
        "old_digest_suffix"
    ):
        raise CutoverBlocked("QA_CUTOVER_OLD_PAIR_IDENTITY_CHANGED")
    old_args = argparse.Namespace(
        source_archive_dir=args.old_source_archive_dir,
        target_commit=args.old_commit,
    )
    identity = {
        "version": version,
        "digest": digest,
        "source_commit": args.old_commit,
    }
    exact = staged_pair_proof_args(old_args, identity)
    proof = SOURCE_PAIR.verify(exact)
    require_signed_source_pair_proof(proof, exact)


def reverify_signed_new_pair(args: argparse.Namespace, record: dict) -> None:
    """Reauthenticate the complete staged source and OCI before cutover."""
    exact = staged_pair_proof_args(args, record)
    proof = SOURCE_PAIR.verify(exact)
    require_signed_source_pair_proof(proof, exact)


def evaluate(args: argparse.Namespace) -> dict:
    """Match the stopped writer and pending rescue to the release pair."""
    check_qa_boundary(args)
    signed = getattr(args, "verify_signatures", False)
    if signed:
        args.new_commit = args.target_commit
    base = BASE.evaluate(args)
    check_base_proof(base, signed)
    target = check_target(args, base)
    if signed:
        reverify_signed_old_pair(args, base)
        reverify_signed_new_pair(args, target)
    return {
        "status": (
            "PASS_QA_CUTOVER_SIGNED_OCI_COHERENCE_ONLY"
            if signed
            else "PASS_QA_CUTOVER_COHERENCE_ONLY"
        ),
        "old_version": base["old_version"],
        "new_version": base["new_version"],
        "target_commit": target["source_commit"],
        "pending_token_matched": True,
        "sqlite_wal_photo_parity": True,
        "source_archive_sha256_matched": True,
        "paired_receipt_consistent": True,
        "source_signature_reverified": signed,
        "old_source_signature_reverified": signed,
        "new_source_signature_reverified": signed,
        "old_oci_signature_reverified": signed,
        "new_oci_signature_reverified": signed,
        "oci_signature_reverified": signed,
        "image_actually_changed": False,
        "release_authorized": False,
        "rollback_authorized": False,
        "production_modified": False,
    }


def main() -> int:
    """Report QA identity only; never clear pending or modify a container."""
    args = arguments()
    try:
        report = evaluate(args)
    except (
        OSError,
        ValueError,
        sqlite3.Error,
        TypeError,
        KeyError,
        subprocess.TimeoutExpired,
        tarfile.TarError,
    ) as exc:
        report = {
            "status": "BLOCKED",
            "reason": str(exc)
            if isinstance(exc, ValueError)
            else "QA_CUTOVER_EVIDENCE_UNAVAILABLE",
            "release_authorized": False,
            "production_modified": False,
        }
    if args.jsonl:
        print(json.dumps(report, separators=(",", ":")))
    else:
        print("QA CUTOVER:", report["status"])
    return 0 if report["status"].startswith("PASS_") else 3


if __name__ == "__main__":
    sys.exit(main())
