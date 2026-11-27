#!/usr/bin/env python3
"""Bind a synthetic incident hold to pinned historical signed-pair evidence."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import subprocess
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "hold", Path(__file__).with_name("qa-incident-hold.py")
)
assert SPEC and SPEC.loader
HOLD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HOLD)
TX = HOLD.TX
BOUND = HOLD.load("qa-bound-offline-pair")
CP = HOLD.load("qa-compose-provenance")
PAIR = BOUND.PAIR
OCI = BOUND.OCI


def receipt_pair(root, receipt_sha):
    """Bind private receipt bytes and their exact signed pair aliases."""
    raw = PAIR.private_bytes(root / "deployed.json", 4096)
    if PAIR.digest(raw) != receipt_sha:
        raise ValueError("receipt anchor mismatch")
    receipt = PAIR.object_bytes(raw)
    pair = {key: receipt[key] for key in PAIR.PAIR_KEYS}
    if (
        receipt["commit"] != pair["source_commit"]
        or receipt["archive_sha256"] != pair["source_sha256"]
    ):
        raise ValueError("receipt alias mismatch")
    return receipt, pair


def check_reference(root, receipt_sha, journal_sha, online_sha):
    """Verify retained reference bytes without inspecting a live writer."""
    receipt, pair = receipt_pair(root, receipt_sha)
    BOUND.check_online(root, {"pair": pair}, online_sha)
    CP.check_journal(root, {"pair": pair}, journal_sha)
    journal_path = root / "transactions" / pair["transaction_token"]
    journal = PAIR.object_bytes(
        PAIR.private_bytes(journal_path / "transaction.json", 65536)
    )
    if set(journal) != {"phase", "pair", "qa_fixture", "supervised"}:
        raise ValueError("unexpected synthetic journal fields")
    proof = OCI.verify_chain(root, pair["digest"], pair["source_commit"])
    image = PAIR.inspect_image(proof["image_id"])
    OCI.verify_local(proof, image)
    PAIR.SUP.verify_receipt(
        root / "deployed.json",
        root / "VERSION",
        True,
        PAIR.SUP.image_revision(image),
        root / "release-archive",
    )
    if receipt_pair(root, receipt_sha) != (receipt, pair):
        raise ValueError("reference receipt changed")
    return {
        "pair": pair,
        "image_id": proof["image_id"],
        "offline_layers_verified": len(proof["diff_ids"]),
    }


def review(args):
    """Revalidate the hold before and after checking reference evidence."""
    common = (
        args.root,
        args.token,
        args.fault,
        args.scope_sha256,
        args.hold_sha256,
    )
    before = HOLD.inspect(*common)
    result = check_reference(
        args.root, args.receipt_sha256, args.journal_sha256, args.online_sha256
    )
    if HOLD.inspect(*common) != before:
        raise ValueError("incident hold changed")
    return {
        **result,
        "status": "PASS_QA_INCIDENT_REFERENCE_ONLY",
        "hold_sha256": args.hold_sha256,
        "reference_receipt_sha256": args.receipt_sha256,
        "reference_journal_sha256": args.journal_sha256,
        "online_evidence_sha256": args.online_sha256,
        "synthetic_journal": True,
        "installed_pair_verified": False,
        "signatures_reverified_offline": False,
        "fault_attribution_verified": False,
        "release_authorized": False,
        "startup_authorized": False,
        "deployment_authorized": False,
    }


def arguments():
    """Require independently supplied exact anchors for every receipt."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--fault", choices=HOLD.FAULTS, required=True)
    for name in ("scope", "hold", "receipt", "journal", "online"):
        parser.add_argument("--" + name + "-sha256", required=True)
    return parser.parse_args()


def main():
    """Use existing offline stopped-writer gates; no launch or release."""
    args = arguments()
    try:
        TX.require_qa(args.root)
        with TX.exclusive(args.root):
            result = review(args)
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        EOFError,
        sqlite3.Error,
        subprocess.SubprocessError,
        TX.WriterFenceError,
        PAIR.SUP.BootBlocked,
    ) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": type(exc).__name__}))
        return 3
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
