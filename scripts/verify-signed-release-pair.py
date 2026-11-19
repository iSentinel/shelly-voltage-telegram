#!/usr/bin/env python3
"""Match signed source and OCI bytes to one exact Git commit, read-only."""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import re
import subprocess
import tarfile
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent
IMAGE = "ghcr.io/isentinel/shelly-voltage-telegram@"


class PairBlocked(ValueError):
    """The immutable release pair could not be safely verified."""


def load_module(name: str, filename: str):
    """Import only the pinned adjacent source and OCI proof code."""
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    if spec is None or spec.loader is None:
        raise PairBlocked("PAIR_VERIFIER_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SOURCE = load_module(
    "qa_source_bundle_pair", "verify-source-release-bundle.py"
)
OCI = load_module("qa_oci_pair", "verify-published-image.py")


def arguments() -> argparse.Namespace:
    """Require explicit immutable bytes, source SHA and OCI digest."""
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--version", required=True)
    cli.add_argument("--commit", required=True)
    cli.add_argument("--digest", required=True)
    cli.add_argument("--archive", type=Path, required=True)
    cli.add_argument("--bundle", type=Path, required=True)
    cli.add_argument("--sha256sums", type=Path, required=True)
    cli.add_argument("--jsonl", action="store_true")
    return cli.parse_args()


def verify_inputs(args: argparse.Namespace) -> None:
    """Reject mutable refs before any network or disk verification."""
    if not SOURCE.VERSION.fullmatch(args.version):
        raise PairBlocked("PAIR_VERSION_INVALID")
    if not re.fullmatch(r"[0-9a-f]{40}", args.commit):
        raise PairBlocked("PAIR_COMMIT_INVALID")
    OCI.require_digest(args.digest)


def verify_image_signature(args: argparse.Namespace) -> None:
    """Verify the signed OCI index against the exact tagged image workflow."""
    identity = (
        "https://github.com/iSentinel/shelly-voltage-telegram/"
        f".github/workflows/image.yml@refs/tags/v{args.version}"
    )
    command = [
        SOURCE.trusted_cosign(),
        "verify",
        "--certificate-identity",
        identity,
        "--certificate-oidc-issuer",
        SOURCE.ISSUER,
        IMAGE + args.digest,
    ]
    result = subprocess.run(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=90,
        check=False,
    )
    if result.returncode:
        raise PairBlocked("PAIR_OCI_SIGNATURE_UNVERIFIED")


def verify_oci_revision_and_attestations(args: argparse.Namespace) -> None:
    """Ensure both architectures and SLSA provenance reference exact SHA."""
    token = OCI.token_for_public_image()
    index = OCI.fetch_object(token, args.digest, "manifests")
    if index.get("mediaType") not in OCI.INDEX_TYPES:
        raise PairBlocked("PAIR_OCI_INDEX_INVALID")
    with redirect_stdout(io.StringIO()):
        OCI.verify_image(token, index, args.commit)


def verify(args: argparse.Namespace) -> dict:
    """Require signed source and signed OCI index on one immutable commit."""
    verify_inputs(args)
    source_args = argparse.Namespace(
        version=args.version,
        expected_commit=args.commit,
        archive=args.archive,
        bundle=args.bundle,
        sha256sums=args.sha256sums,
    )
    source = SOURCE.verify(source_args)
    if (
        source.get("status") != "PASS_SIGNED_SOURCE_BYTES_ONLY"
        or source.get("source_commit_bound") is not True
        or source.get("release_authorized") is not False
    ):
        raise PairBlocked("PAIR_SOURCE_PROOF_INCOMPLETE")
    verify_image_signature(args)
    verify_oci_revision_and_attestations(args)
    return {
        "status": "PASS_SIGNED_SOURCE_OCI_PAIR_ONLY",
        "version": args.version,
        "commit": args.commit,
        "digest_suffix": args.digest[-12:],
        "source_signature_verified": True,
        "oci_signature_verified": True,
        "oci_attestations_verified": True,
        "release_authorized": False,
        "rollback_authorized": False,
        "production_modified": False,
    }


def main() -> int:
    """Emit a non-secret PASS or BLOCKED, never authorize installation."""
    args = arguments()
    try:
        report = verify(args)
    except (
        ValueError,
        OSError,
        subprocess.TimeoutExpired,
        tarfile.TarError,
        KeyError,
        TypeError,
    ) as exc:
        report = {
            "status": "BLOCKED",
            "reason": str(exc)
            if isinstance(exc, (PairBlocked, SOURCE.SourceBlocked))
            else "PAIR_VERIFICATION_UNAVAILABLE",
            "release_authorized": False,
            "rollback_authorized": False,
            "production_modified": False,
        }
    if args.jsonl:
        print(json.dumps(report, separators=(",", ":")))
    else:
        print("SIGNED SOURCE/OCI PAIR:", report["status"])
        if report["status"] == "BLOCKED":
            print("reason:", report["reason"])
    return 0 if report["status"].startswith("PASS_") else 3


if __name__ == "__main__":
    raise SystemExit(main())
