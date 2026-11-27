#!/usr/bin/env python3
"""Review actual synthetic incident artifacts, never authorize recovery."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import stat
from pathlib import Path


def load(name):
    """Load a neighboring reviewed QA helper."""
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).with_name(name + ".py")
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHAIN = load("qa-incident-manifest")
GEN = load("qa-general-recovery-review")


def anchored(root, name, expected):
    """Read exact protected JSON bytes, not an arbitrary supplied path."""
    raw = CHAIN.private_file(root / name)
    if CHAIN.digest(raw) != expected:
        raise ValueError("artifact fingerprint mismatch")
    return CHAIN.decode(raw)


def match(actual, expected):
    """Require exact types and fields, including synthetic scope."""
    if json.dumps(actual, sort_keys=True) != json.dumps(
        expected, sort_keys=True
    ):
        raise ValueError("artifact claim mismatch")


def check_pair(root, identity):
    """Compare synthetic receipt and journal to the declared writer."""
    receipt = anchored(root, "receipt.json", identity["receipt_sha256"])
    match(
        receipt,
        {
            "schema": 1,
            "synthetic": True,
            "image_id": identity["image_id"],
            "signature_verified": False,
        },
    )
    journal = anchored(root, "journal.json", identity["journal_sha256"])
    match(
        journal,
        {
            "schema": 1,
            "synthetic": True,
            "phase": "verified",
            "receipt_sha256": identity["receipt_sha256"],
        },
    )


def check_fault(root, fault):
    """Require matching observation contents, not actual fault attribution."""
    observation = anchored(
        root, "observation.json", fault["observation_sha256"]
    )
    match(
        observation,
        {
            "schema": 1,
            "synthetic": True,
            "identity": fault["identity"],
            "observed_at": fault["observed_at"],
            "fault_class": fault["fault_class"],
            "readiness_http": 503,
        },
    )


def private_database(path):
    """Enforce the root-only synthetic database input boundary."""
    info = path.lstat()
    if (
        info.st_uid != 0
        or stat.S_IMODE(info.st_mode) != 0o600
        or not 0 < info.st_size <= 16 * 1024 * 1024
    ):
        raise ValueError("unsafe synthetic database")


def database_bytes(path):
    """Hash a bounded private standalone QA database."""
    before = GEN.identity(path)
    private_database(path)
    raw = path.read_bytes()
    if len(raw) != before[2] or GEN.identity(path) != before:
        raise ValueError("synthetic database changed")
    return CHAIN.digest(raw)


def check_databases(root, stopped):
    """Recompute logical history and rescue identity on separate copies."""
    live, rescue = root / "live.sqlite3", root / "rescue.sqlite3"
    hashes = [database_bytes(path) for path in (live, rescue)]
    a, b = GEN.snapshot(live), GEN.snapshot(rescue)
    identity = stopped["identity"]
    if (
        a["logical_sha256"] != stopped["history_sha256"]
        or b["logical_sha256"] != a["logical_sha256"]
        or hashes[1] != stopped["rescue_sha256"]
        or a["pending"] != stopped["pending_intents"]
        or (a["device"], a["inode"])
        != (identity["database_device"], identity["database_inode"])
        or (a["device"], a["inode"]) == (b["device"], b["inode"])
    ):
        raise ValueError("synthetic database evidence mismatch")
    if [database_bytes(path) for path in (live, rescue)] != hashes:
        raise ValueError("synthetic database bytes changed")
    return a, hashes


def check_capture(root, stopped, hashes):
    """Only verify a standalone-copy declaration, not a real WAL capture."""
    record = anchored(root, "wal-capture.json", stopped["wal_capture_sha256"])
    match(
        record,
        {
            "schema": 1,
            "synthetic": True,
            "kind": "standalone-no-sidecars",
            "live_sha256": hashes[0],
            "rescue_sha256": hashes[1],
            "history_sha256": stopped["history_sha256"],
            "wal_capture_verified": False,
        },
    )


def check_hold(root, hold, stopped_sha):
    """Require the exact synthetic hold declaration and predecessor."""
    record = anchored(root, "hold-receipt.json", hold["hold_receipt_sha256"])
    match(
        record,
        {
            "schema": 1,
            "synthetic": True,
            "stopped_sha256": stopped_sha,
            "identity": hold["identity"],
            "history_sha256": hold["history_sha256"],
            "release_authorized": False,
        },
    )


def review(root, raw, stages, anchor):
    """Validate actual referenced QA files without trusting their claims."""
    result = CHAIN.check_manifest(raw, stages, anchor)
    records = {stage: CHAIN.decode(value) for stage, value in stages.items()}
    check_pair(root, records["baseline"]["identity"])
    check_fault(root, records["fault"])
    history, hashes = check_databases(root, records["stopped"])
    check_capture(root, records["stopped"], hashes)
    check_hold(root, records["hold"], CHAIN.digest(stages["stopped"]))
    return {
        **result,
        "status": "PASS_SYNTHETIC_ARTIFACT_CONTENTS_ONLY",
        "artifact_contents_verified": True,
        "synthetic_artifacts_only": True,
        "wal_capture_verified": False,
        "incident_hold_verified": False,
        "rows": history["rows"],
        "pending_intents": history["pending"],
    }


def main():
    """Review only a dedicated offline fixture, without changing files."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    args = parser.parse_args()
    try:
        CHAIN.require_qa(args.root)
        raw = CHAIN.private_file(args.root / "manifest.json")
        stages = {
            s: CHAIN.private_file(args.root / (s + ".json"))
            for s in CHAIN.STAGES
        }
        result = review(args.root, raw, stages, args.manifest_sha256)
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": type(exc).__name__}))
        return 3
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
