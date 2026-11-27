#!/usr/bin/env python3
"""Rehearse durable bidirectional mode changes without starting Docker."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
from pathlib import Path


def load(name):
    """Import an existing sibling QA helper."""
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).with_name(name + ".py")
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TX = load("qa-report-mode-transaction")
PLAN = load("qa-same-runtime-recovery-plan")
MARKER = TX.marker_module()
TOKEN = re.compile(r"[a-z0-9]{12}\Z")
KEYS = {
    "schema",
    "token",
    "source",
    "target",
    "old_sha256",
    "new_sha256",
    "baseline",
    "phase",
    "deployment_authorized",
    "startup_authorized",
}


def journal_path(root, token):
    """Confine transaction records to one reviewed fixture."""
    if not TOKEN.fullmatch(token):
        raise ValueError("invalid transaction token")
    return root / ("mode-" + token + ".json")


def marker_token(root, token):
    """Namespace a marker to its exact fixture and transaction."""
    return root.name + "-" + token


def config(root):
    """Read only a private protected configuration."""
    TX.protected(root / ".env")
    return PLAN.private_input(root / ".env", 65536)


def save(root, token, record):
    """Durably publish the phase using the existing atomic writer."""
    TX.atomic_write(
        journal_path(root, token),
        (json.dumps(record, sort_keys=True) + "\n").encode(),
    )


def read(root, token):
    """Reject malformed or authorizing journals."""
    path = journal_path(root, token)
    TX.protected(path)
    record = json.loads(
        PLAN.private_input(path, 8192), object_pairs_hook=PLAN.unique_object
    )
    if (
        set(record) != KEYS
        or type(record["schema"]) is not int
        or record["schema"] != 1
        or record["token"] != token
        or record["phase"] not in ("prepared", "configured", "released")
        or record["source"] not in PLAN.MODES
        or record["target"] not in PLAN.MODES
        or record["source"] == record["target"]
        or record["deployment_authorized"] is not False
        or record["startup_authorized"] is not False
        or any(
            not PLAN.HEX.fullmatch(record[k])
            for k in ("old_sha256", "new_sha256")
        )
    ):
        raise ValueError("invalid mode journal")
    return record


def pending(root, token):
    """Refuse any foreign marker; never adopt or delete it."""
    value = MARKER.marker_value(TX.marker_path(root))
    if value is not None:
        TX.protected(TX.marker_path(root))
        if value != marker_token(root, token):
            raise ValueError("foreign mode marker")
    return value


def check_history(root, record):
    """Require the complete stopped baseline before configuration changes."""
    if TX.history_snapshot(root) != record["baseline"]:
        raise ValueError("stopped history changed")


def target_bytes(root, record):
    """Accept only exact old or new bytes, with reversible reconstruction."""
    raw = config(root)
    if TX.sha(raw) == record["old_sha256"]:
        new = PLAN.transition(raw, record["source"], record["target"])
        old = raw
    elif TX.sha(raw) == record["new_sha256"]:
        old = PLAN.transition(raw, record["target"], record["source"])
        new = raw
    else:
        raise ValueError("mode config drift")
    if (TX.sha(old), TX.sha(new)) != (
        record["old_sha256"],
        record["new_sha256"],
    ):
        raise ValueError("mode config fingerprint mismatch")
    return new


def prepare(root, token, source, target):
    """Write exact stopped intent before publishing the owned fence."""
    if pending(root, token) is not None or os.path.lexists(
        journal_path(root, token)
    ):
        raise ValueError("existing mode transaction")
    for path in root.glob("mode-*.json"):
        previous = read(root, path.stem.removeprefix("mode-"))
        if previous["phase"] != "released":
            raise ValueError("unfinished mode transaction")
    raw = config(root)
    new = PLAN.transition(raw, source, target)
    record = {
        "schema": 1,
        "token": token,
        "source": source,
        "target": target,
        "old_sha256": TX.sha(raw),
        "new_sha256": TX.sha(new),
        "baseline": TX.history_snapshot(root),
        "phase": "prepared",
        "deployment_authorized": False,
        "startup_authorized": False,
    }
    save(root, token, record)
    MARKER.publish(TX.marker_path(root), marker_token(root, token))
    return record


def recover(root, token):
    """Roll forward an owned intent while keeping the writer fenced."""
    record = read(root, token)
    if record["phase"] == "released":
        raise ValueError("released mode transaction cannot recover")
    check_history(root, record)
    new = target_bytes(root, record)
    marker = pending(root, token)
    if marker is None:
        if (
            record["phase"] != "prepared"
            or TX.sha(config(root)) != record["old_sha256"]
        ):
            raise ValueError("missing mode marker")
        MARKER.publish(TX.marker_path(root), marker_token(root, token))
    if record["phase"] == "configured" and config(root) != new:
        raise ValueError("configured mode drift")
    if config(root) != new:
        TX.atomic_write(root / ".env", new)
    check_history(root, record)
    record["phase"] = "configured"
    save(root, token, record)
    return record


def release(root, token):
    """Remove only an owned fence after durable completion; not a start."""
    record = read(root, token)
    if record["phase"] not in ("configured", "released"):
        raise ValueError("mode transaction not configured")
    check_history(root, record)
    if TX.sha(config(root)) != record["new_sha256"]:
        raise ValueError("target config drift")
    marker = pending(root, token)
    if record["phase"] == "configured":
        if marker is None:
            raise ValueError("missing mode marker")
        record["phase"] = "released"
        save(root, token, record)
    if marker is not None:
        MARKER.clear(TX.marker_path(root), marker_token(root, token))
    return record


def arguments():
    """Parse the explicit QA operation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--token", required=True)
    parser.add_argument("action", choices=("prepare", "recover", "release"))
    parser.add_argument("--source", choices=PLAN.MODES)
    parser.add_argument("--target", choices=PLAN.MODES)
    return parser.parse_args()


def main():
    """Execute only under the isolated QA and stopped-writer gates."""
    args = arguments()
    try:
        TX.require_qa(args.root)
        with TX.exclusive(args.root):
            if args.action == "prepare":
                record = prepare(
                    args.root, args.token, args.source, args.target
                )
            else:
                record = {"recover": recover, "release": release}[args.action](
                    args.root, args.token
                )
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        subprocess.SubprocessError,
        TX.WriterFenceError,
    ) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": type(exc).__name__}))
        return 3
    print(
        json.dumps(
            {
                "status": "PASS_QA_MODE_OPERATOR_ONLY",
                "phase": record["phase"],
                "startup_authorized": False,
                "deployment_authorized": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
