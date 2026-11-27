#!/usr/bin/env python3
"""Read-only QA review of a versioned recovery policy and installed pair."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import stat
import subprocess
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "supervisor", Path(__file__).with_name("supervised-container.py")
)
assert SPEC and SPEC.loader
SUP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SUP)
TX = SUP.load_recovery_qa()
PAIR_KEYS = (
    "version",
    "digest",
    "source_commit",
    "source_sha256",
    "transaction_token",
)
POLICY_KEYS = {
    "schema",
    "pair",
    "receipt_sha256",
    "configuration_sha256",
    "container_id",
    "image_id",
    "container_config_sha256",
    "mode",
    "deployment_authorized",
}


def unique_object(pairs: list) -> dict:
    """Reject ambiguous JSON, including nested duplicate keys."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def private_bytes(path: Path, limit: int) -> bytes:
    """Read a bounded private root-controlled file, not a symbolic link."""
    TX.protected(path)
    info = path.lstat()
    if stat.S_IMODE(info.st_mode) != 0o600 or not 0 < info.st_size <= limit:
        raise ValueError("private review input unsafe")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        data = stream.read(limit + 1)
    if not 0 < len(data) <= limit:
        raise ValueError("private review input size")
    return data


def object_bytes(data: bytes) -> dict:
    """Require a single unambiguous object."""
    result = json.loads(data, object_pairs_hook=unique_object)
    if not isinstance(result, dict):
        raise ValueError("review input not an object")
    return result


def digest(data: bytes) -> str:
    """Compute the exact input fingerprint."""
    return hashlib.sha256(data).hexdigest()


def config_digest(info: dict) -> str:
    """Bind Docker Config without emitting environment or command values."""
    config = info["Config"]
    if not isinstance(config, dict):
        raise ValueError("invalid container config")
    return digest(
        json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
    )


def validate_policy(policy: dict) -> None:
    """Require a closed v1 QA schema and no deployment authorization."""
    if (
        set(policy) != POLICY_KEYS
        or type(policy["schema"]) is not int
        or policy["schema"] != 1
        or policy["deployment_authorized"] is not False
        or policy["mode"] != "paused"
        or not isinstance(policy["pair"], dict)
        or set(policy["pair"]) != set(PAIR_KEYS)
    ):
        raise ValueError("unsupported recovery policy schema")
    for key in (
        "receipt_sha256",
        "configuration_sha256",
        "container_id",
        "container_config_sha256",
    ):
        if not isinstance(policy[key], str) or not SUP.SHA256.fullmatch(
            policy[key]
        ):
            raise ValueError("invalid policy fingerprint")
    if not isinstance(policy["image_id"], str) or not SUP.DIGEST.fullmatch(
        policy["image_id"]
    ):
        raise ValueError("invalid policy image identity")


def check_pair(root: Path, policy: dict, info: dict) -> str:
    """Require the exact installed proof and retained source bytes."""
    path = root / "deployed.json"
    raw = private_bytes(path, 4096)
    record = object_bytes(raw)
    if digest(raw) != policy["receipt_sha256"]:
        raise ValueError("receipt bytes changed")
    if any(record.get(key) != policy["pair"][key] for key in PAIR_KEYS):
        raise ValueError("signed pair changed")
    if record.get("commit") != record.get("source_commit") or record.get(
        "archive_sha256"
    ) != record.get("source_sha256"):
        raise ValueError("receipt aliases disagree")
    return SUP.verify_receipt(
        path,
        root / "VERSION",
        True,
        SUP.image_revision(info),
        root / "release-archive",
    )


def check_container(root: Path, policy: dict, info: dict, pin: str) -> None:
    """Refuse a running writer, mutable image reference or code mounts."""
    host = info["HostConfig"]
    if (
        info["Id"] != policy["container_id"]
        or info["Image"] != policy["image_id"]
        or info["Config"]["Image"] != pin
        or config_digest(info) != policy["container_config_sha256"]
        or info["State"]["Running"] is not False
        or host["RestartPolicy"]["Name"] != "no"
        or host["NetworkMode"] != "none"
    ):
        raise ValueError("container policy changed")
    SUP.validate_report_mode(info, "paused")
    mounts = [
        (m["Destination"], m["Source"], m["Type"], m["RW"])
        for m in info["Mounts"]
    ]
    expected = [
        ("/data", str(root / "data"), "bind", True),
        ("/run/svt-fence", str(root / "fence"), "bind", True),
    ]
    if sorted(mounts) != sorted(expected):
        raise ValueError("unexpected QA container mounts")


def check_image(image: dict, policy: dict, pin: str) -> None:
    """Require actual image ID and registry digest, not a revision alone."""
    if (
        image.get("Id") != policy["image_id"]
        or not isinstance(image.get("RepoDigests"), list)
        or pin not in image["RepoDigests"]
        or SUP.image_revision(image) != policy["pair"]["source_commit"]
    ):
        raise ValueError("local OCI identity unconfirmed")


def review(root: Path, info: dict, image: dict) -> dict:
    """Review a stopped fixture without granting any startup authority."""
    policy = object_bytes(private_bytes(root / "signed-policy.json", 8192))
    validate_policy(policy)
    if (
        digest(private_bytes(root / ".env", 65536))
        != policy["configuration_sha256"]
    ):
        raise ValueError("configuration changed")
    pin = check_pair(root, policy, info)
    check_container(root, policy, info, pin)
    check_image(image, policy, pin)
    return {
        "status": "PASS_QA_PAIR_BINDING_ONLY",
        "version": policy["pair"]["version"],
        "schema": 1,
        "signatures_reverified": False,
        "deployment_authorized": False,
        "startup_authorized": False,
    }


def inspect_image(identity: str) -> dict:
    """Read local image metadata without fetching or starting anything."""
    result = SUP.docker("image", "inspect", identity)
    if result.returncode:
        raise ValueError("local image unavailable")
    items = json.loads(result.stdout)
    if (
        not isinstance(items, list)
        or len(items) != 1
        or not isinstance(items[0], dict)
    ):
        raise ValueError("invalid image inspect")
    return items[0]


def main() -> int:
    """Restrict CLI review to the dedicated offline QA transaction layout."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args()
    try:
        TX.require_qa(args.root)
        name = "svt-qa-" + args.root.name
        with TX.exclusive(args.root):
            if SUP._pending_under_gate(args.root / "data/qa.sqlite3"):
                raise ValueError("update pending")
            info = SUP.inspect(name)
            result = review(args.root, info, inspect_image(info["Image"]))
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
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
