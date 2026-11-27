#!/usr/bin/env python3
"""Bind offline image review to independently pinned online QA evidence."""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "offline_oci", Path(__file__).with_name("qa-offline-oci-proof.py")
)
assert SPEC and SPEC.loader
OCI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(OCI)
PAIR = OCI.PAIR
EVIDENCE_KEYS = {
    "schema",
    "version",
    "commit",
    "index_digest",
    "source_sha256",
    "source_signature_verified",
    "oci_signature_verified",
    "oci_attestations_verified",
    "deployment_authorized",
}


def check_online(root: Path, policy: dict, anchor: str) -> None:
    """Require the exact independently supplied online evidence hash."""
    raw = PAIR.private_bytes(root / "online-evidence.json", 8192)
    if not PAIR.SUP.SHA256.fullmatch(anchor) or PAIR.digest(raw) != anchor:
        raise ValueError("online evidence anchor mismatch")
    evidence = PAIR.object_bytes(raw)
    if set(evidence) != EVIDENCE_KEYS or type(evidence["schema"]) is not int:
        raise ValueError("online evidence schema mismatch")
    expected = {
        "schema": 1,
        "version": policy["pair"]["version"],
        "commit": policy["pair"]["source_commit"],
        "index_digest": policy["pair"]["digest"],
        "source_sha256": policy["pair"]["source_sha256"],
        "source_signature_verified": True,
        "oci_signature_verified": True,
        "oci_attestations_verified": True,
        "deployment_authorized": False,
    }
    if evidence != expected or any(
        evidence[key] is not expected[key]
        for key in (
            "source_signature_verified",
            "oci_signature_verified",
            "oci_attestations_verified",
            "deployment_authorized",
        )
    ):
        raise ValueError("online evidence pair mismatch")


def check_host(info: dict) -> None:
    """Require restrictive disposable QA security settings."""
    host = info["HostConfig"]
    if (
        host.get("Privileged") is not False
        or host.get("ReadonlyRootfs") is not True
        or host.get("CapDrop") != ["ALL"]
        or host.get("CapAdd") not in (None, [])
        or host.get("SecurityOpt") != ["no-new-privileges"]
        or host.get("Devices") not in (None, [])
        or host.get("DeviceRequests") not in (None, [])
        or host.get("PortBindings") not in (None, {})
        or host.get("PidMode") not in ("", "private")
        or host.get("IpcMode") not in ("", "private")
        or host.get("PublishAllPorts") is not False
        or info["Config"].get("User") != "10001:10001"
    ):
        raise ValueError("unsafe offline QA host configuration")


def review(root: Path, info: dict, image: dict, anchor: str) -> dict:
    """Recompute all evidence; accept only the explicitly pinned offline ID."""
    policy = PAIR.object_bytes(
        PAIR.private_bytes(root / "signed-policy.json", 8192)
    )
    if type(policy.get("schema")) is not int or policy["schema"] != 2:
        raise ValueError("offline policy schema mismatch")
    PAIR.validate_policy(dict(policy, schema=1))
    check_online(root, policy, anchor)
    env = PAIR.private_bytes(root / ".env", 65536)
    if (
        env != b"SCHEDULED_REPORTS_MODE=paused\n"
        or PAIR.digest(env) != policy["configuration_sha256"]
    ):
        raise ValueError("offline QA configuration changed")
    PAIR.check_pair(root, policy, info)
    PAIR.check_container(root, policy, info, policy["image_id"])
    check_host(info)
    proof = OCI.verify_chain(
        root, policy["pair"]["digest"], policy["pair"]["source_commit"]
    )
    if proof["image_id"] != policy["image_id"]:
        raise ValueError("offline image binding mismatch")
    OCI.verify_local(proof, image)
    return {
        "status": "PASS_BOUND_OFFLINE_PAIR_QA_ONLY",
        "version": policy["pair"]["version"],
        "schema": 2,
        "image_id": proof["image_id"],
        "online_evidence_sha256": anchor,
        "offline_layers_verified": len(proof["diff_ids"]),
        "signatures_reverified_offline": False,
        "startup_authorized": False,
        "deployment_authorized": False,
    }


def main() -> int:
    """Review a dedicated offline fixture under the existing gates."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--online-evidence-sha256", required=True)
    args = parser.parse_args()
    try:
        PAIR.TX.require_qa(args.root)
        with PAIR.TX.exclusive(args.root):
            if PAIR.SUP._pending_under_gate(args.root / "data/qa.sqlite3"):
                raise ValueError("update pending")
            info = PAIR.SUP.inspect("svt-qa-" + args.root.name)
            result = review(
                args.root,
                info,
                PAIR.inspect_image(info["Image"]),
                args.online_evidence_sha256,
            )
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        EOFError,
        subprocess.SubprocessError,
        PAIR.TX.WriterFenceError,
    ) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": type(exc).__name__}))
        return 3
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
