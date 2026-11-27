#!/usr/bin/env python3
"""Check a synthetic incident evidence chain without authorizing recovery."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import stat
from pathlib import Path

HEX = re.compile(r"[a-f0-9]{64}\Z")
STAGES = ("baseline", "fault", "stopped", "hold")
IDENTITY = {
    "container_id",
    "image_id",
    "started_at",
    "receipt_sha256",
    "journal_sha256",
    "database_device",
    "database_inode",
    "writer_lock_device",
    "writer_lock_inode",
}
EXTRA = {
    "baseline": {"writer_lease_held", "readiness_http"},
    "fault": {"fault_class", "observation_sha256"},
    "stopped": {
        "writer_quiescent",
        "history_sha256",
        "rescue_sha256",
        "pending_intents",
        "wal_capture_sha256",
    },
    "hold": {"writer_quiescent", "history_sha256", "hold_receipt_sha256"},
}


def digest(raw):
    """Hash the exact serialized evidence bytes."""
    return hashlib.sha256(raw).hexdigest()


def unique(pairs):
    """Reject duplicate JSON fields at any nesting depth."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate evidence key")
        result[key] = value
    return result


def decode(raw):
    """Require a JSON object and reject ambiguous nonfinite constants."""

    def reject(_value):
        """Refuse nonstandard numeric constants."""
        raise ValueError("nonfinite JSON")

    result = json.loads(raw, object_pairs_hook=unique, parse_constant=reject)
    if not isinstance(result, dict):
        raise ValueError("evidence must be an object")
    return result


def fingerprint(value):
    """Require a canonical SHA256 fingerprint."""
    if not isinstance(value, str) or not HEX.fullmatch(value):
        raise ValueError("invalid evidence fingerprint")


def integer(value, minimum=0):
    """Reject booleans and negative or oversized integers."""
    if type(value) is not int or not minimum <= value <= 2**63 - 1:
        raise ValueError("invalid evidence integer")


def check_identity(value):
    """Require complete writer and database identity fields."""
    if not isinstance(value, dict) or set(value) != IDENTITY:
        raise ValueError("invalid writer identity fields")
    for key in ("container_id", "receipt_sha256", "journal_sha256"):
        fingerprint(value[key])
    image = value["image_id"]
    if not isinstance(image, str) or not image.startswith("sha256:"):
        raise ValueError("invalid image identity")
    fingerprint(image[7:])
    for key in (
        "started_at",
        "database_device",
        "database_inode",
        "writer_lock_device",
        "writer_lock_inode",
    ):
        integer(value[key], 1)


def check_stage_fields(stage, record):
    """Require exact stage fields and explicitly scoped claims."""
    common = {
        "schema",
        "stage",
        "synthetic",
        "observed_at",
        "identity",
        "previous_sha256",
    }
    if (
        set(record) != common | EXTRA[stage]
        or type(record["schema"]) is not int
        or record["schema"] != 1
        or record["stage"] != stage
        or record["synthetic"] is not True
    ):
        raise ValueError("invalid stage schema")
    check_identity(record["identity"])
    integer(record["observed_at"], 1)
    for key in EXTRA[stage]:
        if key.endswith("_sha256"):
            fingerprint(record[key])
    if stage == "baseline" and (
        record["writer_lease_held"] is not True
        or type(record["readiness_http"]) is not int
        or record["readiness_http"] != 200
    ):
        raise ValueError("invalid healthy baseline")
    if stage == "fault" and record["fault_class"] not in (
        "general-runtime",
        "unknown",
    ):
        raise ValueError("invalid incident fault class")
    if stage in ("stopped", "hold") and record["writer_quiescent"] is not True:
        raise ValueError("missing quiescence evidence")
    if stage == "stopped":
        integer(record["pending_intents"])


def check_manifest(raw, stages, anchor):
    """Validate exact hashes, chronology and one unchanged writer identity."""
    fingerprint(anchor)
    if digest(raw) != anchor:
        raise ValueError("manifest anchor mismatch")
    manifest = decode(raw)
    if (
        set(manifest) != {"schema", "synthetic", "stages"}
        or type(manifest["schema"]) is not int
        or manifest["schema"] != 1
        or manifest["synthetic"] is not True
        or not isinstance(manifest["stages"], dict)
        or set(manifest["stages"]) != set(STAGES)
        or set(stages) != set(STAGES)
    ):
        raise ValueError("invalid manifest schema")
    previous, identity, observed, stopped = None, None, 0, None
    for stage in STAGES:
        stage_sha = manifest["stages"][stage]
        fingerprint(stage_sha)
        if digest(stages[stage]) != stage_sha:
            raise ValueError("stage bytes mismatch")
        record = decode(stages[stage])
        check_stage_fields(stage, record)
        identity = record["identity"] if identity is None else identity
        if (
            record["identity"] != identity
            or record["previous_sha256"] != previous
            or record["observed_at"] <= observed
            or record["observed_at"] < identity["started_at"]
        ):
            raise ValueError("incident chain mismatch")
        previous, observed = stage_sha, record["observed_at"]
        if stage == "stopped":
            stopped = record["history_sha256"]
        if stage == "hold" and record["history_sha256"] != stopped:
            raise ValueError("held history mismatch")
    return result_record(anchor)


def result_record(anchor):
    """Explicitly separate structural review from actual incident proof."""
    return {
        "status": "PASS_SYNTHETIC_INCIDENT_MANIFEST_ONLY",
        "manifest_sha256": anchor,
        "stages_checked": len(STAGES),
        "artifact_contents_verified": False,
        "installed_pair_verified": False,
        "fault_attribution_verified": False,
        "writer_quiescence_verified": False,
        "repair_qualified": False,
        "owner_approved": False,
        "release_authorized": False,
        "startup_authorized": False,
        "deployment_authorized": False,
        "rollback_authorized": False,
        "restore_database_authorized": False,
    }


def private_file(path):
    """Read only small, root-owned, protected ordinary files."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != 0
            or stat.S_IMODE(info.st_mode) != 0o600
            or not 0 < info.st_size <= 8192
        ):
            raise ValueError("unsafe evidence file")
        raw = stream.read(8193)
    if len(raw) != info.st_size:
        raise ValueError("evidence size changed")
    return raw


def require_qa(root):
    """Restrict the CLI to dedicated offline synthetic evidence fixtures."""
    if (
        os.geteuid() != 0
        or root.parent != Path("/var/lib")
        or not re.fullmatch(
            r"svt-incident-manifest-qa\.[A-Za-z0-9]{6}", root.name
        )
        or socket.gethostname() != "shelly-docker-reboot-qa"
        or sorted(name for _, name in socket.if_nameindex()) != ["lo"]
    ):
        raise ValueError("offline synthetic QA required")
    for path in (root, *root.parents):
        info = path.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != 0
            or info.st_mode & 0o022
        ):
            raise ValueError("unsafe evidence directory")


def main():
    """Review fixed evidence filenames; never invoke services or Docker."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    args = parser.parse_args()
    try:
        require_qa(args.root)
        raw = private_file(args.root / "manifest.json")
        stages = {s: private_file(args.root / (s + ".json")) for s in STAGES}
        result = check_manifest(raw, stages, args.manifest_sha256)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": type(exc).__name__}))
        return 3
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
