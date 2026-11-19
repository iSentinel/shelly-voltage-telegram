#!/usr/bin/env python3
"""Join QA old/new release identity and newest WAL/PHOTO pending evidence."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import socket
import sqlite3
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
SOURCE_STAGE = Path("/var/lib/svt-qa-verified-pair-20261119")


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


def arguments() -> argparse.Namespace:
    """Require exact isolated old/new evidence; perform no installation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qa-only", action="store_true", required=True)
    parser.add_argument("--source-snapshot", required=True, type=Path)
    parser.add_argument("--signed-snapshot", required=True, type=Path)
    parser.add_argument("--new-receipt", required=True, type=Path)
    parser.add_argument("--new-version", required=True, type=Path)
    parser.add_argument("--source-archive-dir", required=True, type=Path)
    parser.add_argument("--target-commit", required=True)
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


def evaluate(args: argparse.Namespace) -> dict:
    """Match the stopped writer and pending rescue to the release pair."""
    check_qa_boundary(args)
    base = BASE.evaluate(args)
    if (
        base.get("status") != "PASS_QA_IDENTITY_AND_WAL_ONLY"
        or base.get("sqlite_wal_photo_parity") is not True
        or base.get("release_authorized") is not False
    ):
        raise CutoverBlocked("QA_CUTOVER_BASE_PROOF_INCOMPLETE")
    target = check_target(args, base)
    return {
        "status": "PASS_QA_CUTOVER_COHERENCE_ONLY",
        "old_version": base["old_version"],
        "new_version": base["new_version"],
        "target_commit": target["source_commit"],
        "pending_token_matched": True,
        "sqlite_wal_photo_parity": True,
        "source_archive_sha256_matched": True,
        "paired_receipt_consistent": True,
        "source_signature_reverified": False,
        "oci_signature_reverified": False,
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
    except (OSError, ValueError, sqlite3.Error, TypeError, KeyError) as exc:
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
