#!/usr/bin/env python3
"""Bind a durable QA transition to one immutable Docker START request."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "mode_operator", Path(__file__).with_name("qa-durable-mode-operator.py")
)
assert SPEC and SPEC.loader
OP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(OP)
TX = OP.TX
BOUND = OP.load("qa-bound-offline-pair")
IMAGE = (
    "sha256:6b3f263cb444ce6b7424fd14563c85cb5a82253ad8ac74eecb7469d0bbe38541"
)
COMMAND = ["-c", "import time; time.sleep(120)"]


def hash_object(value):
    """Hash deterministic JSON without exposing configuration values."""
    return TX.sha(json.dumps(value, sort_keys=True).encode())


def path_for(root, token, kind):
    """Use only token-scoped records in the protected fixture."""
    OP.journal_path(root, token)
    return root / f"start-{token}-{kind}.json"


def read_bytes(path):
    """Read a private bounded record under the protected path contract."""
    TX.protected(path)
    return OP.PLAN.private_input(path, 8192)


def write_once(path, value):
    """Do not replace a previously recorded intent or consumed claim."""
    if os.path.lexists(path):
        raise ValueError("START record already exists")
    TX.atomic_write(path, (json.dumps(value, sort_keys=True) + "\n").encode())


def validate(root, info, record):
    """Require only the fixed offline identity-witness container profile."""
    host, cfg = info["HostConfig"], info["Config"]
    if (
        not OP.PLAN.HEX.fullmatch(info["Id"])
        or info["Image"] != IMAGE
        or cfg["Image"] != IMAGE
        or info["State"]["Running"] is not False
        or info["State"]["StartedAt"] != "0001-01-01T00:00:00Z"
        or host["NetworkMode"] != "none"
        or host["RestartPolicy"]["Name"] != "no"
        or cfg["Entrypoint"] != ["python"]
        or cfg["Cmd"] != COMMAND
        or [
            v
            for v in cfg["Env"]
            if v.split("=", 1)[0] == "SCHEDULED_REPORTS_MODE"
        ]
        != ["SCHEDULED_REPORTS_MODE=" + record["target"]]
    ):
        raise ValueError("unreviewed stopped witness")
    BOUND.check_host(info)
    mounts = sorted(
        (m["Destination"], m["Source"], m["Type"], m["RW"])
        for m in info["Mounts"]
    )
    expected = sorted(
        (target, str(root / name), "bind", True)
        for name, target in (("data", "/data"), ("fence", "/run/svt-fence"))
    )
    if mounts != expected:
        raise ValueError("witness mounts changed")


def binding(root, token, journal_sha, *, validator=None):
    """Recheck stopped history, exact journal, marker and Docker identity."""
    raw = read_bytes(OP.journal_path(root, token))
    if TX.sha(raw) != journal_sha:
        raise ValueError("journal anchor mismatch")
    record = OP.read(root, token)
    if record["phase"] != "configured" or OP.pending(root, token) is None:
        raise ValueError("configured owned fence required")
    OP.check_history(root, record)
    if TX.sha(OP.config(root)) != record["new_sha256"]:
        raise ValueError("target config drift")
    info = TX.inspect_qa_container(root, "svt-qa-" + root.name)
    extra = (validator or validate)(root, info, record) or {}
    return {
        **extra,
        "schema": 1,
        "journal_sha256": journal_sha,
        "container_id": info["Id"],
        "image_id": info["Image"],
        "config_sha256": hash_object(info["Config"]),
        "host_config_sha256": hash_object(info["HostConfig"]),
        "mounts_sha256": hash_object(info["Mounts"]),
        "startup_authorized": False,
        "deployment_authorized": False,
    }


def arm(root, token, journal_sha, *, bind=None):
    """Record immutable reviewed target while the fence is still present."""
    record = (bind or binding)(root, token, journal_sha)
    path = path_for(root, token, "arm")
    write_once(path, record)
    return {"arm_sha256": TX.sha(read_bytes(path)), **record}


def claim(root, token, arm_sha, *, bind=None):
    """Consume the exact externally pinned arm before releasing the fence."""
    path = path_for(root, token, "claim")
    if os.path.lexists(path):
        raise ValueError("START already claimed")
    raw = read_bytes(path_for(root, token, "arm"))
    if TX.sha(raw) != arm_sha:
        raise ValueError("arm anchor mismatch")
    record = json.loads(raw, object_pairs_hook=OP.PLAN.unique_object)
    if (bind or binding)(root, token, record["journal_sha256"]) != record:
        raise ValueError("armed witness changed")
    write_once(
        path,
        {
            "arm_sha256": arm_sha,
            "container_id": record["container_id"],
            "deployment_authorized": False,
        },
    )
    OP.release(root, token, claim_sha=TX.sha(read_bytes(path)))
    return record["container_id"]


def start(root, token, anchor, *, bind=None):
    """Keep both gates while issuing one immutable Docker request."""
    with TX.exclusive(root, observation=True):
        with TX.quiescent_writer_lease(root / "data/qa.sqlite3"):
            identity = claim(root, token, anchor, bind=bind)
        subprocess.run(
            ["docker", "start", identity],
            check=True,
            capture_output=True,
            timeout=20,
        )
    return {"container_id": identity, "claim_consumed": True}


def main():
    """Restrict all actions to an offline disposable QA witness."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--token", required=True)
    parser.add_argument("--anchor", required=True)
    parser.add_argument("action", choices=("arm", "start"))
    args = parser.parse_args()
    try:
        TX.require_qa(args.root)
        if args.action == "arm":
            with TX.exclusive(args.root):
                result = arm(args.root, args.token, args.anchor)
        else:
            result = start(args.root, args.token, args.anchor)
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
                **result,
                "status": "PASS_QA_WITNESS_ONLY",
                "startup_authorized": False,
                "deployment_authorized": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
