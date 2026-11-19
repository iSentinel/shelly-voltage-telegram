#!/usr/bin/env python3
"""Cross-check an offline signed-pair fixture and newest WAL rescue for QA."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sqlite3
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent


class PairBlocked(ValueError):
    """Stable rejection reason for an incomplete QA transaction."""


def load_helper(filename: str, name: str) -> object:
    """Load a source-only preflight helper from this pinned source tree."""
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    if spec is None or spec.loader is None:
        raise PairBlocked("QA_HELPER_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PAIR = load_helper("signed-rollback-pair-preflight.py", "svt_old_pair")
STAGE = load_helper("stage-guarded-update.py", "svt_qa_stage_pair")
OWNER = load_helper("supervised-container.py", "svt_qa_owner_pair")


def arguments() -> argparse.Namespace:
    """Require complete explicit isolated QA paths and no release actions."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qa-only", action="store_true", required=True)
    parser.add_argument("--source-snapshot", required=True, type=Path)
    parser.add_argument("--signed-snapshot", required=True, type=Path)
    parser.add_argument("--new-receipt", required=True, type=Path)
    parser.add_argument("--new-version", required=True, type=Path)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--fence-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--container", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--jsonl", action="store_true")
    return parser.parse_args()


def private_qa_path(path: Path) -> None:
    """Refuse any signed fixture outside a dedicated /var/lib/svt-qa-*."""
    base = Path("/var/lib")
    if not path.is_absolute():
        raise PairBlocked("QA_SIGNED_PATH_INVALID")
    try:
        relative = path.relative_to(base)
    except ValueError as exc:
        raise PairBlocked("QA_SIGNED_PATH_INVALID") from exc
    if not relative.parts or not relative.parts[0].startswith("svt-qa-"):
        raise PairBlocked("QA_SIGNED_PATH_INVALID")
    if any(part in ("..", ".") for part in relative.parts):
        raise PairBlocked("QA_SIGNED_PATH_INVALID")


def evaluate(args: argparse.Namespace) -> dict:
    """Require simultaneous old-pair identity and newest backup parity."""
    if os.geteuid() != 0 or not args.qa_only:
        raise PairBlocked("QA_ROOT_ONLY")
    for path in (
        args.source_snapshot,
        args.signed_snapshot,
        args.new_receipt,
        args.new_version,
    ):
        private_qa_path(path)
    STAGE.check_paths(args)
    info = STAGE.inspect_container(args.container)
    STAGE.check_container(info, args)
    STAGE.inspect_stage(args, info)
    old_pair = PAIR.check_pair(
        args.source_snapshot, args.signed_snapshot, verify_signature=False
    )
    if old_pair.get("signature_verified") is not False:
        raise PairBlocked("QA_PAIR_SIGNATURE_STATUS_UNEXPECTED")
    target = OWNER.source_version(args.new_version)
    if target != old_pair["target_version"]:
        raise PairBlocked("QA_PAIR_TARGET_VERSION_MISMATCH")
    image = OWNER.verify_receipt(args.new_receipt, args.new_version)
    if not image.startswith(OWNER.IMAGE + "sha256:"):
        raise PairBlocked("QA_PAIR_NEW_IMAGE_INVALID")
    return {
        "status": "PASS_QA_IDENTITY_AND_WAL_ONLY",
        "old_version": old_pair["source_version"],
        "new_version": target,
        "old_digest_suffix": old_pair["old_digest_suffix"],
        "new_digest_suffix": image[-12:],
        "sqlite_wal_photo_parity": True,
        "old_oci_signature_verified": False,
        "new_oci_signature_verified": False,
        "release_authorized": False,
        "rollback_authorized": False,
        "production_modified": False,
    }


def main() -> int:
    """Never interpret QA coherence as a signed or deployable release."""
    args = arguments()
    try:
        result = evaluate(args)
    except (OSError, ValueError, sqlite3.Error) as exc:
        code = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        print(f"FAIL: QA_PAIR_RESCUE_BLOCKED {code}", file=sys.stderr)
        return 3
    if args.jsonl:
        print(json.dumps(result, separators=(",", ":")))
    else:
        print("PASS: QA identity and WAL/PHOTO parity only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
