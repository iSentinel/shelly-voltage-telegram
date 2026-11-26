#!/usr/bin/env python3
"""Audit a newly signed forward-only repair without authorizing deployment."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent


class RepairBlocked(ValueError):
    """A candidate is not an authenticated, forward-only repair."""


def sibling(filename: str, name: str):
    """Load pinned adjacent verification helpers without trusting PATH."""
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    if spec is None or spec.loader is None:
        raise RepairBlocked("REPAIR_HELPER_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SIGNED = sibling("verify-signed-release-pair.py", "svt_forward_signed_pair")
MOUNTS = sibling("qa-image-runtime-mount-audit.py", "svt_forward_mounts")


def arguments() -> argparse.Namespace:
    """Require two immutable image identities and real candidate assets."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qa-only", action="store_true", required=True)
    parser.add_argument("--current-version", required=True)
    parser.add_argument("--current-digest", required=True)
    parser.add_argument("--candidate-version", required=True)
    parser.add_argument("--candidate-commit", required=True)
    parser.add_argument("--candidate-digest", required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--sha256sums", type=Path, required=True)
    parser.add_argument("--mounts-json", type=Path, required=True)
    parser.add_argument("--jsonl", action="store_true")
    return parser.parse_args()


def version_tuple(value: str) -> tuple[int, int, int]:
    """Reject mutable labels, release candidates and malformed versions."""
    if SIGNED.SOURCE.VERSION.fullmatch(value) is None:
        raise RepairBlocked("REPAIR_VERSION_INVALID")
    return tuple(int(x) for x in value.removesuffix("-hw").split("."))


def verify_candidate(args: argparse.Namespace) -> None:
    """Authenticate a complete new signed source and OCI at exact SHA."""
    pair = argparse.Namespace(
        version=args.candidate_version,
        commit=args.candidate_commit,
        digest=args.candidate_digest,
        archive=args.archive,
        bundle=args.bundle,
        sha256sums=args.sha256sums,
    )
    try:
        proof = SIGNED.verify(pair)
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        tarfile.TarError,
        subprocess.TimeoutExpired,
    ) as exc:
        raise RepairBlocked("REPAIR_SIGNED_PAIR_UNVERIFIED") from exc
    if not isinstance(proof, dict):
        raise RepairBlocked("REPAIR_SIGNED_PAIR_INCOMPLETE")
    required = (
        "source_signature_verified",
        "oci_signature_verified",
        "oci_attestations_verified",
    )
    if (
        proof.get("status") != "PASS_SIGNED_SOURCE_OCI_PAIR_ONLY"
        or proof.get("version") != pair.version
        or proof.get("commit") != pair.commit
        or proof.get("digest_suffix") != pair.digest[-12:]
        or any(proof.get(key) is not True for key in required)
    ):
        raise RepairBlocked("REPAIR_SIGNED_PAIR_INCOMPLETE")


def evaluate(args: argparse.Namespace) -> dict:
    """Bind the authenticated candidate to a code-free runtime layout."""
    if getattr(args, "qa_only", False) is not True:
        raise RepairBlocked("REPAIR_QA_ONLY_REQUIRED")
    if version_tuple(args.candidate_version) <= version_tuple(
        args.current_version
    ):
        raise RepairBlocked("REPAIR_FORWARD_VERSION_REQUIRED")
    if re.fullmatch(r"sha256:[0-9a-f]{64}", args.current_digest) is None:
        raise RepairBlocked("REPAIR_CURRENT_DIGEST_INVALID")
    if args.current_digest == args.candidate_digest:
        raise RepairBlocked("REPAIR_NEW_IMAGE_REQUIRED")
    verify_candidate(args)
    try:
        layout = MOUNTS.check_mounts(MOUNTS.read_mounts(args.mounts_json))
    except (OSError, ValueError, TypeError) as exc:
        raise RepairBlocked("REPAIR_RUNTIME_LAYOUT_UNVERIFIED") from exc
    if (
        not isinstance(layout, dict)
        or layout.get("status") != "PASS_RUNTIME_MOUNT_LAYOUT_EVIDENCE_ONLY"
    ):
        raise RepairBlocked("REPAIR_RUNTIME_LAYOUT_INCOMPLETE")
    return {
        "status": "PASS_SIGNED_FORWARD_REPAIR_EVIDENCE_ONLY",
        "candidate_version": args.candidate_version,
        "candidate_commit": args.candidate_commit,
        "candidate_digest_suffix": args.candidate_digest[-12:],
        "new_source_and_oci_signatures_verified": True,
        "code_bind_mounts_absent": True,
        "old_oci_trust_not_inherited": True,
        "mount_inventory_attested": False,
        "newest_wal_photo_pending_verified": False,
        "sole_writer_cutover_verified": False,
        "boot_recovery_verified": False,
        "release_authorized": False,
        "rollback_authorized": False,
        "production_modified": False,
    }


def main() -> int:
    """Report non-authorizing evidence and fail closed on bad input."""
    try:
        args = arguments()
        report = evaluate(args)
    except (RepairBlocked, OSError, TypeError) as exc:
        report = {
            "status": "BLOCKED",
            "reason": (
                str(exc)
                if isinstance(exc, RepairBlocked)
                else "REPAIR_PREFLIGHT_UNAVAILABLE"
            ),
            "release_authorized": False,
            "rollback_authorized": False,
            "production_modified": False,
        }
    if args.jsonl:
        print(json.dumps(report, separators=(",", ":")))
    else:
        print("SIGNED FORWARD REPAIR:", report["status"])
        if report["status"] == "BLOCKED":
            print("reason:", report["reason"])
    return 0 if report["status"].startswith("PASS_") else 3


if __name__ == "__main__":
    raise SystemExit(main())
