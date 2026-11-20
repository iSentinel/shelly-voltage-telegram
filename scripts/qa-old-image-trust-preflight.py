#!/usr/bin/env python3
"""Audit an old immutable OCI image without authorizing a rollback."""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent


class OldImageBlocked(ValueError):
    """A missing trust property blocks old-image-only recovery."""


def load_verifier():
    """Reuse the exact signed-image and OCI attestation gate."""
    path = ROOT / "verify-signed-release-pair.py"
    spec = importlib.util.spec_from_file_location("svt_old_oci_pair", path)
    if spec is None or spec.loader is None:
        raise OldImageBlocked("OLD_OCI_VERIFIER_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VERIFY = load_verifier()


def arguments() -> argparse.Namespace:
    """Require immutable old release identity and an explicit QA flag."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qa-only", action="store_true", required=True)
    parser.add_argument("--old-version", dest="version", required=True)
    parser.add_argument("--old-commit", dest="commit", required=True)
    parser.add_argument("--old-digest", dest="digest", required=True)
    parser.add_argument("--jsonl", action="store_true")
    return parser.parse_args()


def verify_old_image(args: argparse.Namespace) -> dict:
    """Check the old digest signature and exact-revision OCI attestations."""
    if getattr(args, "qa_only", False) is not True:
        raise OldImageBlocked("OLD_OCI_QA_ONLY_REQUIRED")
    try:
        VERIFY.verify_inputs(args)
    except (ValueError, TypeError) as exc:
        raise OldImageBlocked("OLD_OCI_IDENTITY_INVALID") from exc
    try:
        result = VERIFY.verify_image_signature(args)
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        raise OldImageBlocked("OLD_OCI_SIGNATURE_UNVERIFIED") from exc
    if result is not None:
        raise OldImageBlocked("OLD_OCI_SIGNATURE_PROOF_INVALID")
    try:
        result = VERIFY.verify_oci_revision_and_attestations(args)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise OldImageBlocked("OLD_OCI_ATTESTATIONS_UNVERIFIED") from exc
    if result is not None:
        raise OldImageBlocked("OLD_OCI_ATTESTATION_PROOF_INVALID")
    return {
        "status": "PASS_OLD_OCI_TRUST_EVIDENCE_ONLY",
        "old_version": args.version,
        "old_commit": args.commit,
        "old_digest_suffix": args.digest[-12:],
        "old_oci_signature_verified": True,
        "old_oci_attestations_verified": True,
        "old_source_signature_verified": False,
        "sqlite_wal_photo_parity_verified": False,
        "sole_writer_cutover_verified": False,
        "release_authorized": False,
        "rollback_authorized": False,
        "production_modified": False,
    }


def main() -> int:
    """Print non-authorizing evidence; never change any Docker state."""
    args = arguments()
    try:
        report = verify_old_image(args)
    except (OldImageBlocked, OSError) as exc:
        report = {
            "status": "BLOCKED",
            "reason": (
                str(exc)
                if isinstance(exc, OldImageBlocked)
                else "OLD_OCI_CHECK_UNAVAILABLE"
            ),
            "release_authorized": False,
            "rollback_authorized": False,
            "production_modified": False,
        }
    if args.jsonl:
        print(json.dumps(report, separators=(",", ":")))
    else:
        print("OLD OCI IMAGE-ONLY TRUST:", report["status"])
        if report["status"] == "BLOCKED":
            print("reason:", report["reason"])
    return 0 if report["status"].startswith("PASS_") else 3


if __name__ == "__main__":
    raise SystemExit(main())
