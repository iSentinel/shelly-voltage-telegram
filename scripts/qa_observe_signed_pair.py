#!/usr/bin/env python3
"""Observe a signed pair for a QA journal, never authorize installation."""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
from pathlib import Path

from cutover_journal import Journal, JournalBlocked, digest, validate_identity

ROOT = Path(__file__).resolve().parent


def verifier():
    """Load the existing adjacent verifier, not a caller-supplied report."""
    spec = importlib.util.spec_from_file_location(
        "svt_qa_observe_postsign",
        ROOT / "qa-postsign-release-acceptance.py",
    )
    if spec is None or spec.loader is None:
        raise JournalBlocked("SIGNED_PAIR_VERIFIER_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_identity(args: argparse.Namespace, identity: dict) -> None:
    """Reject mutable or conflicting identity before verification."""
    validate_identity(identity)
    if (
        args.qa_only is not True
        or args.version != identity["version"]
        or args.commit != identity["commit"]
        or args.digest != identity["oci_digest"]
    ):
        raise JournalBlocked("SIGNED_PAIR_IDENTITY_MISMATCH")


def check_report(report: dict, identity: dict) -> None:
    """Require full digest and exact signed proof, not merely a PASS label."""
    expected = {
        "status": "PASS_QA_POSTSIGN_PUBLIC_PAIR_EVIDENCE_ONLY",
        "version": identity["version"],
        "source_commit": identity["commit"],
        "source_sha256": identity["source_sha256"],
        "oci_digest": identity["oci_digest"],
    }
    true_fields = (
        "signed_source_verified",
        "signed_oci_verified",
        "oci_attestations_verified",
        "public_asset_inventory_matched",
    )
    false_fields = (
        "owner_approval_verified",
        "sole_writer_cutover_verified",
        "release_authorized",
        "rollback_authorized",
        "production_modified",
    )
    if (
        not isinstance(report, dict)
        or any(report.get(k) != v for k, v in expected.items())
        or any(report.get(k) is not True for k in true_fields)
        or any(report.get(k) is not False for k in false_fields)
    ):
        raise JournalBlocked("SIGNED_PAIR_OBSERVATION_INCOMPLETE")


def observe(args: argparse.Namespace, identity: dict) -> dict:
    """Perform the real read-only checks for the exact requested pair."""
    verify_identity(args, identity)
    report = verifier().evaluate(args)
    check_report(report, identity)
    return {
        "status": "PASS_QA_SIGNED_PAIR_OBSERVATION_ONLY",
        "identity": identity,
        "evidence_sha256": digest(report),
        "report": report,
        "release_authorized": False,
        "production_modified": False,
    }


def record_observation(journal: Journal, args: argparse.Namespace) -> dict:
    """Record proof only for a pending verification intent."""
    status = journal.inspect()
    if (
        status["decision"] != "RECONCILE_REQUIRED"
        or status["stage"] != "verify_signed_pair"
    ):
        raise JournalBlocked("SIGNED_PAIR_INTENT_REQUIRED")
    result = observe(args, journal.identity)
    journal.append("observe", "verify_signed_pair", result["evidence_sha256"])
    return result


def arguments() -> argparse.Namespace:
    """Expose read-only verification only; no production journal CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qa-only", action="store_true", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--digest", required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--token", required=True)
    for name in (
        "release-json",
        "tag-ref-json",
        "archive",
        "bundle",
        "sha256sums",
    ):
        parser.add_argument("--" + name, type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    """Emit a QA report with no permission to release or deploy."""
    args = arguments()
    identity = {
        "version": args.version,
        "commit": args.commit,
        "source_sha256": args.source_sha256,
        "oci_digest": args.digest,
        "token": args.token,
    }
    try:
        result = observe(args, identity)
    except (ValueError, OSError, TypeError, subprocess.TimeoutExpired):
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "release_authorized": False,
                    "production_modified": False,
                }
            )
        )
        return 3
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
