#!/usr/bin/env python3
"""Review exact same-runtime mode transition inputs without writing config."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import stat
from pathlib import Path

MODE = b"SCHEDULED_REPORTS_MODE"
MODES = ("enabled", "paused")
HEX = re.compile(r"[a-f0-9]{64}\Z")
COMMIT = re.compile(r"[a-f0-9]{40}\Z")
TOKEN = re.compile(r"cutover-[A-Za-z0-9_]{8}\Z")
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+-hw\Z")
ASSIGN = re.compile(rb"^([A-Za-z_][A-Za-z0-9_]*)=")
PAIR_FIELDS = (
    "version",
    "digest",
    "source_commit",
    "source_sha256",
    "transaction_token",
)


def sha(value: bytes) -> str:
    """Hash private bytes without returning their contents."""
    return hashlib.sha256(value).hexdigest()


def transition(raw: bytes, source: str, target: str) -> bytes:
    """Preserve every unrelated byte and reject ambiguous mode syntax."""
    if (
        source not in MODES
        or target not in MODES
        or source == target
        or not 0 < len(raw) <= 65536
        or not raw.endswith(b"\n")
        or b"\x00" in raw
        or b"\r" in raw
    ):
        raise ValueError("unsupported mode transition input")
    raw.decode("utf-8")
    entries = []
    keys = set()
    lines = raw.splitlines(keepends=True)
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith(b"#") or not stripped.strip():
            continue
        match = ASSIGN.match(line)
        if match is None or match.group(1) in keys:
            raise ValueError("unsupported configuration syntax")
        keys.add(match.group(1))
        if MODE in line and match.group(1) != MODE:
            raise ValueError("indirect mode reference")
        if match.group(1) == MODE:
            entries.append(index)
        elif b"'" in line or b'"' in line or b"\\" in line or b"$" in line:
            raise ValueError("complex configuration requires manual review")
    if (
        len(entries) != 1
        or lines[entries[0]] != MODE + b"=" + source.encode() + b"\n"
    ):
        raise ValueError("mode assignment not canonical or ambiguous")
    lines[entries[0]] = MODE + b"=" + target.encode() + b"\n"
    return b"".join(lines)


def pair_identity(receipt: dict) -> dict:
    """Require canonical installed pair fields and matching proof aliases."""
    pair = {key: receipt[key] for key in PAIR_FIELDS}
    if (
        not VERSION.fullmatch(pair["version"])
        or not COMMIT.fullmatch(pair["source_commit"])
        or not HEX.fullmatch(pair["source_sha256"])
        or not HEX.fullmatch(pair["digest"].removeprefix("sha256:"))
        or not pair["digest"].startswith("sha256:")
        or not TOKEN.fullmatch(pair["transaction_token"])
        or receipt.get("commit") != pair["source_commit"]
        or receipt.get("archive_sha256") != pair["source_sha256"]
        or receipt.get("pair_status") != "PASS_SIGNED_SOURCE_OCI_PAIR_ONLY"
        or any(
            receipt.get(key) is not True
            for key in (
                "source_signature_verified",
                "oci_signature_verified",
                "oci_attestations_verified",
            )
        )
    ):
        raise ValueError("installed pair identity invalid")
    return pair


def plan(
    receipt: dict,
    raw: bytes,
    source: str,
    target: str,
    image: str,
    container: str,
    device: int,
    inode: int,
) -> dict:
    """Bind the proposed transition; this record grants no authority."""
    if (
        not HEX.fullmatch(image.removeprefix("sha256:"))
        or not image.startswith("sha256:")
        or not HEX.fullmatch(container)
        or type(device) is not int
        or device < 0
        or type(inode) is not int
        or inode <= 0
    ):
        raise ValueError("runtime identity invalid")
    desired = transition(raw, source, target)
    return {
        "schema": 1,
        "pair": pair_identity(receipt),
        "source_mode": source,
        "target_mode": target,
        "source_config_sha256": sha(raw),
        "target_config_sha256": sha(desired),
        "image_id": image,
        "container_id": container,
        "database_device": device,
        "database_inode": inode,
        "deployment_authorized": False,
        "startup_authorized": False,
    }


def private_input(path: Path, limit: int) -> bytes:
    """Read only a bounded, existing private regular fixture file."""
    info = path.lstat()
    if (
        not path.is_absolute()
        or any(p.is_symlink() for p in (path, *path.parents))
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid != 0
        or stat.S_IMODE(info.st_mode) != 0o600
        or not 0 < info.st_size <= limit
    ):
        raise ValueError("unsafe private fixture input")
    return path.read_bytes()


def unique_object(items: list) -> dict:
    """Reject repeated JSON fields in private fixture evidence."""
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate fixture field")
        result[key] = value
    return result


def fixture_plan(args: argparse.Namespace) -> dict:
    """Require the dedicated protected offline fixture before reading."""
    root = args.fixture
    if (
        os.geteuid() != 0
        or root.parent != Path("/var/lib")
        or not root.name.startswith("svt-report-mode-tx-qa.")
    ):
        raise ValueError("dedicated QA fixture required")
    if socket.gethostname() != "shelly-docker-reboot-qa" or sorted(
        name for _, name in socket.if_nameindex()
    ) != ["lo"]:
        raise ValueError("offline QA host required")
    for parent in (root, *root.parents):
        info = parent.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != 0
            or info.st_mode & 0o022
        ):
            raise ValueError("unsafe fixture directory")
    receipt = json.loads(
        private_input(root / "deployed.json", 8192),
        object_pairs_hook=unique_object,
    )
    runtime = json.loads(
        private_input(root / "runtime-plan-input.json", 8192),
        object_pairs_hook=unique_object,
    )
    return plan(
        receipt,
        private_input(root / ".env", 65536),
        args.source_mode,
        args.target_mode,
        **runtime,
    )


def main() -> int:
    """Use synthetic inputs only; never change a running installation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qa-only", action="store_true", required=True)
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--source-mode", choices=MODES, required=True)
    parser.add_argument("--target-mode", choices=MODES, required=True)
    args = parser.parse_args()
    try:
        result = fixture_plan(args)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": type(exc).__name__}))
        return 3
    canonical = json.dumps(
        result, sort_keys=True, separators=(",", ":")
    ).encode()
    print(
        json.dumps(
            {
                "status": "PASS_QA_MODE_PLAN_ONLY",
                "plan": result,
                "plan_sha256": sha(canonical),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
