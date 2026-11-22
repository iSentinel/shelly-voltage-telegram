#!/usr/bin/env python3
"""Prepare a non-authorizing owner review packet from exact QA evidence."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
from pathlib import Path

SHA = re.compile(r"[a-f0-9]{40}\Z")
DIGEST = re.compile(r"[a-f0-9]{64}\Z")
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+-hw\Z")
PRIOR_COMMIT = re.compile(
    r"latest checked source commit ([a-f0-9]{40})\s+"
    r"at the time of approval\."
)
PYTHON_VERSIONS = ("3.11", "3.12", "3.13")


class ReviewBlocked(ValueError):
    """The read-only owner review packet cannot be assembled safely."""


def arguments() -> argparse.Namespace:
    """Require explicit private local evidence and exact release identity."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qa-only", action="store_true", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--ci-snapshot", type=Path, required=True)
    parser.add_argument("--historical-approval", type=Path, required=True)
    parser.add_argument("--jsonl", action="store_true")
    return parser.parse_args()


def read_regular(path: Path, limit: int, *, private: bool) -> str:
    """Read a size-bounded, single-link file without trusting symlinks."""
    if not path.is_absolute() or ".." in path.parts:
        raise ReviewBlocked("OWNER_EVIDENCE_PATH_INVALID")
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ReviewBlocked("OWNER_EVIDENCE_LINKED_PATH")
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or not 0 < info.st_size <= limit
    ):
        raise ReviewBlocked("OWNER_EVIDENCE_FILE_UNSAFE")
    if private and (
        info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600
    ):
        raise ReviewBlocked("OWNER_PRIVATE_EVIDENCE_UNSAFE")
    with path.open("r", encoding="utf-8") as stream:
        return stream.read(limit + 1)


def manifest_identity(args: argparse.Namespace) -> dict:
    """Read the candidate source SHA/bytes but not forge authentication."""
    if (
        not args.qa_only
        or not VERSION.fullmatch(args.version)
        or not SHA.fullmatch(args.commit)
    ):
        raise ReviewBlocked("OWNER_REVIEW_IDENTITY_INVALID")
    data = json.loads(read_regular(args.source_manifest, 8192, private=True))
    if not isinstance(data, dict):
        raise ReviewBlocked("OWNER_SOURCE_MANIFEST_INVALID")
    if (
        data.get("status") != "PASS_QA_EXACT_SOURCE_BYTES_ONLY"
        or data.get("source_commit") != args.commit
        or data.get("version") != args.version
        or not isinstance(data.get("source_sha256"), str)
        or not DIGEST.fullmatch(data["source_sha256"])
        or data.get("git_source_reproducible") is not True
        or data.get("source_sigstore_verified") is not False
        or data.get("release_authorized") is not False
    ):
        raise ReviewBlocked("OWNER_SOURCE_SCOPE_MISMATCH")
    return data


def ci_snapshot(args: argparse.Namespace) -> dict:
    """Check QA CI job metadata shape but never authenticate a JSON export."""
    data = json.loads(read_regular(args.ci_snapshot, 32768, private=True))
    if not isinstance(data, dict):
        raise ReviewBlocked("OWNER_CI_SNAPSHOT_INVALID")
    if (
        data.get("headSha") != args.commit
        or data.get("status") != "completed"
        or data.get("conclusion") != "success"
    ):
        raise ReviewBlocked("OWNER_CI_SNAPSHOT_NOT_GREEN")
    jobs = data.get("jobs")
    if not isinstance(jobs, list) or not 4 <= len(jobs) <= 30:
        raise ReviewBlocked("OWNER_CI_JOBS_INVALID")
    names = {}
    for job in jobs:
        if not isinstance(job, dict) or not isinstance(job.get("name"), str):
            raise ReviewBlocked("OWNER_CI_JOB_INVALID")
        name = job["name"]
        if name in names:
            raise ReviewBlocked("OWNER_CI_JOB_DUPLICATED")
        names[name] = job
    for name in ("resolve", *(f"test ({v})" for v in PYTHON_VERSIONS)):
        job = names.get(name)
        if not isinstance(job, dict) or (
            job.get("status") != "completed"
            or job.get("conclusion") != "success"
        ):
            raise ReviewBlocked("OWNER_CI_MATRIX_INCOMPLETE")
    return data


def prior_approval_scope(args: argparse.Namespace) -> str:
    """Identify old in-principle approval without repurposing its scope."""
    text = read_regular(args.historical_approval, 32768, private=False)
    match = PRIOR_COMMIT.search(text)
    if (
        not match
        or "GRANTED in principle" not in text
        or "not yet established" not in text
        or "implicitly approved" not in text
    ):
        raise ReviewBlocked("OWNER_PRIOR_APPROVAL_SCOPE_UNKNOWN")
    return match.group(1)


def evaluate(args: argparse.Namespace) -> dict:
    """Return a narrow NO-GO review packet, never an approval credential."""
    source = manifest_identity(args)
    ci_snapshot(args)
    prior = prior_approval_scope(args)
    return {
        "status": "PASS_OWNER_REVIEW_PACKET_ONLY",
        "decision": "NO_GO",
        "version": args.version,
        "candidate_commit": args.commit,
        "source_sha256": source["source_sha256"],
        "historical_scope_commit": prior,
        "historical_approval_is_release_specific": False,
        "historical_approval_authorizes_candidate": False,
        "ci_snapshot_metadata_matches_candidate": True,
        "ci_snapshot_independently_authenticated": False,
        "new_source_sigstore_verified": False,
        "new_oci_signature_verified": False,
        "new_oci_attestations_verified": False,
        "new_release_specific_owner_approval": False,
        "sole_writer_cutover_verified": False,
        "release_authorized": False,
        "rollback_authorized": False,
        "production_modified": False,
    }


def main() -> int:
    """Print one JSONL/report with stable PASS/BLOCKED exit codes."""
    args = arguments()
    try:
        result = evaluate(args)
    except (OSError, ValueError, TypeError, UnicodeError) as exc:
        result = {
            "status": "BLOCKED",
            "decision": "NO_GO",
            "reason": (
                str(exc)
                if isinstance(exc, ReviewBlocked)
                else "OWNER_REVIEW_EVIDENCE_UNAVAILABLE"
            ),
            "release_authorized": False,
            "rollback_authorized": False,
            "production_modified": False,
        }
    if args.jsonl:
        print(json.dumps(result, separators=(",", ":")))
    else:
        print("OWNER RELEASE REVIEW:", result["status"])
        print("decision:", result["decision"])
        if result["status"] == "BLOCKED":
            print("reason:", result["reason"])
    return 0 if result["status"].startswith("PASS_") else 3


if __name__ == "__main__":
    raise SystemExit(main())
