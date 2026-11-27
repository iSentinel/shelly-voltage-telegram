#!/usr/bin/env python3
"""Persist immutable QA launch and acceptance evidence under caller gates."""

from __future__ import annotations

import json
import os
import time


def save_launch(start, root, token, anchor, identity):
    """Pin StartedAt immediately after the sole Docker START request."""
    info = start.TX.inspect_qa_container(root, "svt-qa-" + root.name)
    state = info["State"]
    if (
        info["Id"] != identity
        or state["Running"] is not True
        or state["StartedAt"] == "0001-01-01T00:00:00Z"
        or info["RestartCount"] != 0
    ):
        raise ValueError("launch identity unavailable")
    record = {
        "schema": 1,
        "arm_sha256": anchor,
        "container_id": identity,
        "started_at": state["StartedAt"],
        "deployment_authorized": False,
    }
    start.write_once(start.path_for(root, token, "launch"), record)
    return record


def read(start, root, token, kind):
    """Decode exact protected receipt bytes without duplicate keys."""
    path = start.path_for(root, token, kind)
    return json.loads(
        start.read_bytes(path), object_pairs_hook=start.OP.PLAN.unique_object
    )


def check_launch(start, root, token, anchor, info):
    """Refuse missing launch evidence or a subsequent container start."""
    expected = {
        "schema": 1,
        "arm_sha256": anchor,
        "container_id": info["Id"],
        "started_at": info["State"]["StartedAt"],
        "deployment_authorized": False,
    }
    actual = read(start, root, token, "launch")
    if actual != expected or info["RestartCount"] != 0:
        raise ValueError("launch receipt mismatch")
    return actual


def accept(start, root, token, anchor, result, history):
    """Record the first fresh observation and preserve its full row prefix."""
    path = start.path_for(root, token, "acceptance")
    launch = start.path_for(root, token, "launch")
    record = {
        "schema": 1,
        "arm_sha256": anchor,
        "launch_sha256": start.TX.sha(start.read_bytes(launch)),
        "container_id": result["container_id"],
        "started_at": result["started_at"],
        "history": history,
        "phases": result["phases"],
        "observed_at": time.time(),
        "qa_runtime_verified": True,
        "deployment_authorized": False,
        "startup_authorized": False,
    }
    if os.path.lexists(path):
        previous = read(start, root, token, "acceptance")
        verify_prior(start, root, previous, record)
        return previous
    start.write_once(path, record)
    return record


def verify_prior(start, root, prior, current):
    """Require stable launch and retained accepted history on replay."""
    stable = (
        "schema",
        "arm_sha256",
        "launch_sha256",
        "container_id",
        "started_at",
        "qa_runtime_verified",
        "deployment_authorized",
        "startup_authorized",
    )
    if set(prior) != set(current) or any(
        prior[k] != current[k] for k in stable
    ):
        raise ValueError("acceptance identity mismatch")
    old = prior["history"]
    new = start.TX.history_snapshot(root, old["last_id"])
    if (
        new["rows"] < old["rows"]
        or new["last_id"] < old["last_id"]
        or any(new[k] != old[k] for k in ("device", "inode", "prefix_sha256"))
    ):
        raise ValueError("accepted history changed")
