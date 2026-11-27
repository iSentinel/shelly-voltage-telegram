#!/usr/bin/env python3
"""Hold a stopped synthetic fixture; never release or start a writer."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sqlite3
from pathlib import Path


def load(name):
    """Load the adjacent reviewed QA helper."""
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).with_name(name + ".py")
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


OP = load("qa-durable-mode-operator")
GEN = load("qa-general-recovery-review")
TX = OP.TX
MARKER = OP.MARKER
HEX = re.compile(r"[a-f0-9]{64}\Z")
FAULTS = ("general-runtime", "unknown")


def token_value(root, token):
    """Namespace the hold distinctly from mode-change transactions."""
    OP.journal_path(root, token)
    return root.name + "-incident-" + token


def hold_path(root):
    """Allow only one hold journal per disposable incident fixture."""
    return root / "incident-hold.json"


def current_marker(root):
    """Read only a protected existing shared startup marker."""
    path = TX.marker_path(root)
    if os.path.lexists(path):
        TX.protected(path)
    return MARKER.marker_value(path)


def snapshot(root, token, fault, anchor):
    """Bind full standalone history and exact external source-review hash."""
    if fault not in FAULTS or not HEX.fullmatch(anchor):
        raise ValueError("invalid incident review inputs")
    source = root / "scope-review.json"
    TX.protected(source)
    if TX.sha(OP.PLAN.private_input(source, 8192)) != anchor:
        raise ValueError("scope review anchor mismatch")
    return {
        "schema": 1,
        "token": token_value(root, token),
        "fault": fault,
        "scope_review_sha256": anchor,
        "fault_operator_supplied": True,
        "configuration_sha256": TX.sha(OP.config(root)),
        "history": GEN.snapshot(root / "data/qa.sqlite3"),
        "release_authorized": False,
        "startup_authorized": False,
        "deployment_authorized": False,
    }


def read_hold(root):
    """Decode the exact protected record without duplicate fields."""
    path = hold_path(root)
    TX.protected(path)
    raw = OP.PLAN.private_input(path, 8192)
    return raw, json.loads(raw, object_pairs_hook=OP.PLAN.unique_object)


def prepare(root, token, fault, anchor):
    """Fence first; an interrupted orphan marker needs manual review."""
    if os.path.lexists(hold_path(root)) or current_marker(root) is not None:
        raise ValueError("existing incident or foreign marker")
    record = snapshot(root, token, fault, anchor)
    MARKER.publish(TX.marker_path(root), record["token"])
    if snapshot(root, token, fault, anchor) != record:
        raise ValueError("incident evidence changed")
    TX.atomic_write(
        hold_path(root), (json.dumps(record, sort_keys=True) + "\n").encode()
    )
    return record


def inspect(root, token, fault, anchor, receipt_sha):
    """Recheck the exact receipt and hold; never recreate a missing fence."""
    raw, record = read_hold(root)
    if TX.sha(raw) != receipt_sha:
        raise ValueError("incident receipt anchor mismatch")
    expected = snapshot(root, token, fault, anchor)
    if record != expected or current_marker(root) != expected["token"]:
        raise ValueError("incident hold evidence changed")
    return record


def arguments():
    """Expose only preparation and read-only hold review."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "inspect"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--fault", choices=FAULTS, required=True)
    parser.add_argument("--scope-sha256", required=True)
    parser.add_argument("--receipt-sha256")
    return parser.parse_args()


def execute(args):
    """Operate only under the isolated stopped-writer QA gates."""
    TX.require_qa(args.root)
    with TX.exclusive(args.root):
        common = (args.root, args.token, args.fault, args.scope_sha256)
        if args.action == "prepare":
            result = prepare(*common)
        else:
            result = inspect(*common, args.receipt_sha256)
        raw, _ = read_hold(args.root)
        return {**result, "receipt_sha256": TX.sha(raw)}


def main():
    """Emit local QA evidence, not production authorization."""
    args = arguments()
    try:
        result = execute(args)
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        sqlite3.Error,
        TX.WriterFenceError,
    ) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": type(exc).__name__}))
        return 3
    print(
        json.dumps(
            {
                **result,
                "status": "PASS_QA_INCIDENT_HOLD_ONLY",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
