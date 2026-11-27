#!/usr/bin/env python3
"""Review a protected QA transaction and actual resolved Compose plan."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "bound_pair", Path(__file__).with_name("qa-bound-offline-pair.py")
)
assert SPEC and SPEC.loader
BOUND = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BOUND)
PAIR = BOUND.PAIR
TOKEN = re.compile(r"cutover-[A-Za-z0-9_]{8}\Z")
INPUTS = ("signed-policy.json", "compose.yaml", "compose-resolved.json")


def load_private(root: Path, name: str) -> dict:
    """Decode a protected bounded JSON object."""
    return PAIR.object_bytes(PAIR.private_bytes(root / name, 65536))


def check_journal(root: Path, policy: dict, expected: str) -> None:
    """Verify a token-scoped QA journal and its retained signed source."""
    receipt = load_private(root, "deployed.json")
    token = policy["pair"]["transaction_token"]
    if not isinstance(token, str) or not TOKEN.fullmatch(token):
        raise ValueError("invalid cutover token")
    work = root / "transactions" / token
    PAIR.TX.protected(work, directory=True)
    raw = PAIR.private_bytes(work / "transaction.json", 65536)
    if PAIR.digest(raw) != expected:
        raise ValueError("journal fingerprint mismatch")
    journal = PAIR.object_bytes(raw)
    if (
        journal.get("phase") != "verified"
        or journal.get("supervised") is not True
        or journal.get("pair") != receipt
        or journal.get("qa_fixture") is not True
    ):
        raise ValueError("transaction provenance mismatch")
    PAIR.SUP.verify_paired_source_archive(receipt, work / "release-archive")


def resolved_plan(root: Path) -> dict:
    """Resolve the protected fixture with a clean environment."""
    result = subprocess.run(
        (
            "docker",
            "compose",
            "--project-directory",
            str(root),
            "--env-file",
            str(root / ".env"),
            "-f",
            str(root / "compose.yaml"),
            "config",
            "--format",
            "json",
        ),
        env={
            "PATH": (
                "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
            ),
            "HOME": "/root",
            "DOCKER_HOST": "unix:///var/run/docker.sock",
        },
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if result.returncode or len(result.stdout) > 65536:
        raise ValueError("Compose resolution failed")
    return PAIR.object_bytes(result.stdout.encode())


def expected_service(root: Path, image_id: str) -> dict:
    """Define a deliberately closed two-mount, no-command-override profile."""
    return {
        "container_name": "svt-qa-" + root.name,
        "image": image_id,
        "pull_policy": "never",
        "restart": "no",
        "network_mode": "none",
        "read_only": True,
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges"],
        "user": "10001:10001",
        "ipc": "private",
        "entrypoint": None,
        "command": None,
        "environment": {"SCHEDULED_REPORTS_MODE": "paused"},
        "volumes": [
            {
                "type": "bind",
                "source": str(root / name),
                "target": target,
                "bind": {"create_host_path": True},
            }
            for name, target in (
                ("data", "/data"),
                ("fence", "/run/svt-fence"),
            )
        ],
    }


def check_plan(root: Path, plan: dict, image_id: str) -> None:
    """Refuse additional services, mounts, environment or execution options."""
    project = re.sub(r"[^a-z0-9_-]", "", root.name.lower())
    expected = {
        "name": project,
        "services": {"shelly-voltage": expected_service(root, image_id)},
    }
    if plan != expected:
        raise ValueError("effective Compose profile mismatch")


def env_map(entries: list) -> dict:
    """Reject malformed and duplicate effective environment entries."""
    values = {}
    for entry in entries:
        key, separator, value = entry.partition("=")
        if not key or not separator or key in values:
            raise ValueError("ambiguous effective environment")
        values[key] = value
    return values


def check_runtime(info: dict, image: dict, service: dict) -> None:
    """Compare effective environment and image-default process with inspect."""
    config, defaults = info["Config"], image["Config"]
    expected = env_map(defaults.get("Env") or [])
    expected.update(service["environment"])
    if env_map(config["Env"]) != expected or any(
        config.get(key) != defaults.get(key)
        for key in (
            "Entrypoint",
            "Cmd",
            "WorkingDir",
            "Healthcheck",
            "StopSignal",
            "Volumes",
        )
    ):
        raise ValueError("effective container config mismatch")
    host = info["HostConfig"]
    if (
        host.get("CgroupnsMode") != "private"
        or host.get("Runtime") != "runc"
        or host.get("UsernsMode") != ""
        or any(
            host.get(key) not in (None, [], {})
            for key in (
                "VolumesFrom",
                "GroupAdd",
                "Sysctls",
                "Tmpfs",
                "ExtraHosts",
                "DeviceCgroupRules",
                "Links",
            )
        )
    ):
        raise ValueError("additional host configuration mismatch")


def review(
    root: Path, info: dict, image: dict, online: str, anchor: str
) -> dict:
    """Bind both independent evidence anchors without creating authority."""
    raw = PAIR.private_bytes(root / "provenance.json", 8192)
    if not PAIR.SUP.SHA256.fullmatch(anchor) or PAIR.digest(raw) != anchor:
        raise ValueError("provenance anchor mismatch")
    provenance = PAIR.object_bytes(raw)
    if (
        set(provenance) != {"schema", "inputs", "journal_sha256", "qa_fixture"}
        or type(provenance["schema"]) is not int
        or provenance["schema"] != 1
        or provenance["qa_fixture"] is not True
        or set(provenance["inputs"]) != set(INPUTS)
    ):
        raise ValueError("provenance schema mismatch")
    for name in INPUTS:
        if (
            PAIR.digest(PAIR.private_bytes(root / name, 65536))
            != provenance["inputs"][name]
        ):
            raise ValueError("provenance input changed")
    policy = load_private(root, "signed-policy.json")
    result = BOUND.review(root, info, image, online)
    check_journal(root, policy, provenance["journal_sha256"])
    plan = resolved_plan(root)
    if plan != load_private(root, "compose-resolved.json"):
        raise ValueError("resolved Compose changed")
    check_plan(root, plan, policy["image_id"])
    check_runtime(info, image, plan["services"]["shelly-voltage"])
    return {
        **result,
        "status": "PASS_QA_COMPOSE_PROVENANCE_ONLY",
        "provenance_sha256": anchor,
        "production_receipt_verified": False,
    }


def main() -> int:
    """Review only the isolated stopped fixture under the existing locks."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--online-evidence-sha256", required=True)
    parser.add_argument("--provenance-sha256", required=True)
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
                args.provenance_sha256,
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
