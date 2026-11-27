#!/usr/bin/env python3
"""Run online pair verification and emit full-identity QA evidence."""

import hashlib
import importlib.util
import json
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "pair_verifier", Path(__file__).with_name("verify-signed-release-pair.py")
)
assert SPEC and SPEC.loader
PAIR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PAIR)


def archive_hash(path: Path) -> str:
    """Hash retained source bytes without reading them into memory."""
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main() -> int:
    """Emit an evidence record only after exact pair verification succeeds."""
    args = PAIR.arguments()
    try:
        source_hash = archive_hash(args.archive)
        result = PAIR.verify(args)
        if (
            result["status"] != "PASS_SIGNED_SOURCE_OCI_PAIR_ONLY"
            or source_hash != archive_hash(args.archive)
            or any(
                result.get(key) is not True
                for key in (
                    "source_signature_verified",
                    "oci_signature_verified",
                    "oci_attestations_verified",
                )
            )
        ):
            raise ValueError("pair verification incomplete")
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": type(exc).__name__}))
        return 3
    print(
        json.dumps(
            {
                "schema": 1,
                "version": args.version,
                "commit": args.commit,
                "index_digest": args.digest,
                "source_sha256": source_hash,
                "source_signature_verified": True,
                "oci_signature_verified": True,
                "oci_attestations_verified": True,
                "deployment_authorized": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
