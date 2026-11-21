#!/usr/bin/env python3
"""Observe an existing fenced QA rescue without restoring or replacing it."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from cutover_journal import Journal, JournalBlocked, digest
from qa_observe_legacy_stop import observe as observe_legacy

ROOT = Path(__file__).resolve().parent


def inspect_rescue(args: argparse.Namespace) -> None:
    """Re-run the existing verifier against the actual stopped QA writer."""
    cmd = [
        sys.executable,
        "-B",
        str(ROOT / "stage-guarded-update.py"),
        "--qa-only",
        "--inspect",
        "--container",
        args.container,
        "--database",
        str(args.database),
        "--fence-dir",
        str(args.fence_dir),
        "--output",
        str(args.output),
        "--token",
        args.token,
    ]
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=45, check=False
    )
    if result.returncode or result.stdout.strip() != (
        "INSPECT PASS: pending, stopped writer, WAL and PHOTO match"
    ):
        raise JournalBlocked("RESCUE_LIVE_INSPECTION_FAILED")


def hash_rescue(path: Path) -> str:
    """Reference the actual verified backup bytes, not caller JSON."""
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def observe(args: argparse.Namespace) -> dict:
    """Verify the selected owner and rescue twice for stable evidence."""
    if args.qa_only is not True or args.require_stopped is not True:
        raise JournalBlocked("RESCUE_STOPPED_QA_REQUIRED")
    before = observe_legacy(args)
    inspect_rescue(args)
    checksum = hash_rescue(args.output)
    inspect_rescue(args)
    after = observe_legacy(args)
    if before != after or checksum != hash_rescue(args.output):
        raise JournalBlocked("RESCUE_OBSERVATION_CHANGED")
    return {
        "status": "PASS_QA_RESCUE_OBSERVATION_ONLY",
        "token": args.token,
        "container_id": after["container_id"],
        "container_image": after["container_image"],
        "rescue_sha256": checksum,
        "logical_history_parity": True,
        "pending_token_verified": True,
        "all_writers_verified": False,
        "release_authorized": False,
        "rollback_authorized": False,
        "production_modified": False,
    }


def record_observation(journal: Journal, args: argparse.Namespace) -> dict:
    """Record only the matching pending rescue stage of the QA journal."""
    state = journal.inspect()
    if (
        state["decision"] != "RECONCILE_REQUIRED"
        or state["stage"] != "capture_newest_rescue"
        or journal.identity["token"] != args.token
    ):
        raise JournalBlocked("RESCUE_INTENT_MISMATCH")
    result = observe(args)
    journal.append("observe", "capture_newest_rescue", digest(result))
    return result


def main() -> int:
    """Inspect only, and leave the existing marker and backup intact."""
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--qa-only", action="store_true", required=True)
    for name in ("unit", "container", "image", "token"):
        cli.add_argument("--" + name, required=True)
    for name in ("database", "fence-dir", "output"):
        cli.add_argument("--" + name, required=True, type=Path)
    cli.set_defaults(require_stopped=True)
    args = cli.parse_args()
    try:
        report = observe(args)
    except (ValueError, OSError, subprocess.TimeoutExpired):
        print(json.dumps({"status": "BLOCKED", "release_authorized": False}))
        return 3
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
