#!/usr/bin/env python3
"""Join independently verified signed source/OCI and public asset inventory."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import subprocess
import tarfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
COMMIT = re.compile(r"[a-f0-9]{40}\Z")
DIGEST = re.compile(r"sha256:[a-f0-9]{64}\Z")
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+-hw\Z")


class PostSignBlocked(ValueError):
    """The independent post-sign release evidence is incomplete."""


def adjacent(filename: str, name: str):
    """Load local, pinned verifiers instead of importing arbitrary modules."""
    spec = importlib.util.spec_from_file_location(name, SCRIPT_DIR / filename)
    if spec is None or spec.loader is None:
        raise PostSignBlocked("POSTSIGN_CHECKER_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


INVENTORY = adjacent("qa-release-asset-inventory.py", "svt_postsign_inventory")
PAIR = adjacent("verify-signed-release-pair.py", "svt_postsign_pair")


def arguments() -> argparse.Namespace:
    """Require only explicit QA inputs; no production or network actions."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qa-only", action="store_true", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--digest", required=True)
    parser.add_argument("--release-json", type=Path, required=True)
    parser.add_argument("--tag-ref-json", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--sha256sums", type=Path, required=True)
    parser.add_argument("--jsonl", action="store_true")
    return parser.parse_args()


def require_identity(args: argparse.Namespace) -> None:
    """Reject mutable tags, arbitrary image names and short source SHAs."""
    if (
        getattr(args, "qa_only", False) is not True
        or VERSION.fullmatch(args.version) is None
        or COMMIT.fullmatch(args.commit) is None
        or DIGEST.fullmatch(args.digest) is None
    ):
        raise PostSignBlocked("POSTSIGN_IMMUTABLE_IDENTITY_REQUIRED")


def require_inventory(args: argparse.Namespace) -> None:
    """Check public artifact names and tag SHA before network verification."""
    try:
        proof = INVENTORY.check(args)
    except (OSError, ValueError, TypeError) as exc:
        raise PostSignBlocked("POSTSIGN_PUBLIC_INVENTORY_UNVERIFIED") from exc
    if (
        not isinstance(proof, dict)
        or proof.get("status") != "PASS_PUBLIC_INVENTORY_ONLY"
        or proof.get("version") != args.version
        or proof.get("tag_commit_matches_input") is not True
        or proof.get("commit_format_valid") is not True
        or proof.get("digest_format_valid") is not True
        or proof.get("release_authorized") is not False
    ):
        raise PostSignBlocked("POSTSIGN_INVENTORY_INCOMPLETE")


def require_signed_pair(args: argparse.Namespace) -> None:
    """Call real signed source and OCI validators tied to one exact SHA."""
    try:
        proof = PAIR.verify(args)
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        tarfile.TarError,
        subprocess.TimeoutExpired,
    ) as exc:
        raise PostSignBlocked("POSTSIGN_SIGNED_PAIR_UNVERIFIED") from exc
    required = (
        "source_signature_verified",
        "oci_signature_verified",
        "oci_attestations_verified",
    )
    if (
        not isinstance(proof, dict)
        or proof.get("status") != "PASS_SIGNED_SOURCE_OCI_PAIR_ONLY"
        or proof.get("version") != args.version
        or proof.get("commit") != args.commit
        or proof.get("digest") != args.digest
        or proof.get("digest_suffix") != args.digest[-12:]
        or proof.get("release_authorized") is not False
        or proof.get("rollback_authorized") is not False
        or any(proof.get(key) is not True for key in required)
    ):
        raise PostSignBlocked("POSTSIGN_SIGNED_PAIR_INCOMPLETE")


def evaluate(args: argparse.Namespace) -> dict:
    """Join source/OCI proof; never return permission to deploy or publish."""
    require_identity(args)
    require_inventory(args)
    require_signed_pair(args)
    with args.archive.open("rb") as stream:
        source_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
    return {
        "status": "PASS_QA_POSTSIGN_PUBLIC_PAIR_EVIDENCE_ONLY",
        "version": args.version,
        "source_commit": args.commit,
        "source_sha256": source_sha256,
        "oci_digest": args.digest,
        "signed_source_verified": True,
        "signed_oci_verified": True,
        "oci_attestations_verified": True,
        "public_asset_inventory_matched": True,
        "public_inventory_authenticated_independently": False,
        "source_git_rebuild_parity_verified_here": False,
        "owner_approval_verified": False,
        "sole_writer_cutover_verified": False,
        "release_authorized": False,
        "rollback_authorized": False,
        "production_modified": False,
    }


def main() -> int:
    """Emit one stable QA decision without installing or starting anything."""
    args = arguments()
    try:
        report = evaluate(args)
    except (OSError, ValueError, TypeError) as exc:
        report = {
            "status": "BLOCKED",
            "reason": (
                str(exc)
                if isinstance(exc, PostSignBlocked)
                else "POSTSIGN_EVIDENCE_UNAVAILABLE"
            ),
            "release_authorized": False,
            "rollback_authorized": False,
            "production_modified": False,
        }
    if args.jsonl:
        print(json.dumps(report, separators=(",", ":")))
    else:
        print("POSTSIGN RELEASE QA:", report["status"])
        if report["status"] == "BLOCKED":
            print("reason:", report["reason"])
    return 0 if report["status"].startswith("PASS_") else 3


if __name__ == "__main__":
    raise SystemExit(main())
