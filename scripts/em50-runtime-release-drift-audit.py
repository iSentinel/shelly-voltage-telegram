#!/usr/bin/env python3
"""Compare running Shelly OCI source, Compose pin, and updater metadata."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

DEFAULT_ROOT = Path("/opt/shelly-voltage")
DEFAULT_OVERRIDE = Path("/var/lib/shelly-voltage-updater/override.yaml")
DEFAULT_YASNO = Path("/etc/shelly-voltage/yasno-reader.yaml")
DEFAULT_META = Path("/var/lib/shelly-voltage-updater/deployed.json")
OFFICIAL = "ghcr.io/isentinel/shelly-voltage-telegram@"


def command_output(args: list[str], cwd: Path | None = None) -> str:
    """Execute a bounded local Docker inspection without a shell."""

    result = subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError("Docker inspection failed")
    if len(result.stdout) > 1048576:
        raise ValueError("Docker inspection output exceeded limit")
    return result.stdout.strip()


def load_deployed(path: Path) -> dict[str, str]:
    """Read the recorded official release identity, never secrets."""

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Updater metadata is not an object")
    digest = data.get("digest")
    version = data.get("version")
    if not isinstance(digest, str) or not re.fullmatch(
        r"sha256:[a-f0-9]{64}", digest
    ):
        raise ValueError("Updater digest is invalid")
    if not isinstance(version, str) or not re.fullmatch(
        r"[0-9]+\.[0-9]+\.[0-9]+-hw", version
    ):
        raise ValueError("Updater version is invalid")
    return {"digest": digest, "version": version}


def compose_image(args: argparse.Namespace) -> str:
    """Resolve the pinned Compose image without exposing environment."""

    files = ["-f", str(args.project_root / "compose.yaml")]
    files.extend(["-f", str(args.override)])
    if args.yasno_overlay.is_file():
        files.extend(["-f", str(args.yasno_overlay)])
    command = ["docker", "compose", *files, "config", "--format", "json"]
    parsed = json.loads(command_output(command, args.project_root))
    image = parsed["services"]["shelly-voltage"]["image"]
    if not isinstance(image, str) or not image:
        raise ValueError("Compose image missing")
    return image


def inspect_runtime(container: str) -> dict[str, str | list[str]]:
    """Read one container and referenced image, without Docker writes."""

    fields = "{{.Image}}|{{.Config.Image}}|{{.State.Status}}"
    fields += "|{{.State.Health.Status}}"
    output = command_output(["docker", "inspect", container, "--format", fields])
    parts = output.split("|")
    if len(parts) != 4:
        raise ValueError("Unexpected container inspection format")
    image_id, config_image, state, health = parts
    manifest = json.loads(
        command_output(
            ["docker", "image", "inspect", config_image,
             "--format", "{{json .RepoDigests}}"]
        )
    )
    local_id = command_output(
        ["docker", "image", "inspect", config_image, "--format", "{{.Id}}"]
    )
    sha_line = command_output(
        ["docker", "exec", container, "sha256sum", "/app/app.py"]
    )
    source_sha = sha_line.split()[0]
    if not re.fullmatch(r"[a-f0-9]{64}", source_sha):
        raise ValueError("Runtime source hash has invalid format")
    if not isinstance(manifest, list):
        raise ValueError("Runtime image digests are malformed")
    return {
        "image_id": image_id,
        "config_image": config_image,
        "state": state,
        "health": health,
        "local_id": local_id,
        "repo_digests": manifest,
        "source_sha": source_sha,
    }


def check_identity(
    runtime: dict, pinned: str, deployed: dict, expected_sha: str
) -> list[str]:
    """Detect boot pin drift and unaudited local images."""

    issues = []
    if runtime["state"] != "running" or runtime["health"] != "healthy":
        issues.append("container_not_healthy")
    if runtime["image_id"] != runtime["local_id"]:
        issues.append("running_image_id_mismatch")
    if runtime["config_image"] != pinned:
        issues.append("compose_runtime_image_mismatch")
    if not pinned.startswith(OFFICIAL + "sha256:"):
        issues.append("unpublished_local_image")
    if pinned != OFFICIAL + deployed["digest"]:
        issues.append("updater_pin_mismatch")
    if OFFICIAL + deployed["digest"] not in runtime["repo_digests"]:
        issues.append("registry_digest_not_verified")
    if runtime["source_sha"] != expected_sha:
        issues.append("runtime_source_hash_mismatch")
    return issues


def inspect(args: argparse.Namespace) -> dict:
    """Inspect immutable identities only; never authorize a release."""

    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", args.container):
        raise ValueError("Invalid container name")
    if not re.fullmatch(r"[a-f0-9]{64}", args.expected_app_sha256):
        raise ValueError("Invalid expected application SHA256")
    deployed = load_deployed(args.deployed)
    runtime = inspect_runtime(args.container)
    pinned = compose_image(args)
    issues = check_identity(
        runtime, pinned, deployed, args.expected_app_sha256
    )
    if not args.override.is_file():
        issues.append("updater_override_missing")
    return {
        "status": "PASS" if not issues else "DRIFT",
        "reason_codes": issues,
        "container": args.container,
        "running_image_id": runtime["image_id"],
        "runtime_source_sha256": runtime["source_sha"],
        "updater_version": deployed["version"],
        "updater_digest": deployed["digest"],
        "runtime_healthy": runtime["health"] == "healthy",
        "release_authorized": False,
        "signature_verified": False,
        "offhost_backup_verified": False,
        "read_only": True,
    }


def main() -> int:
    """Print a stable PASS/DRIFT/ERROR without changing production."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--override", type=Path, default=DEFAULT_OVERRIDE)
    parser.add_argument("--yasno-overlay", type=Path, default=DEFAULT_YASNO)
    parser.add_argument("--deployed", type=Path, default=DEFAULT_META)
    parser.add_argument("--container", default="shelly-voltage-bot")
    parser.add_argument("--expected-app-sha256", required=True)
    parser.add_argument("--jsonl", action="store_true")
    args = parser.parse_args()
    try:
        report = inspect(args)
    except (ValueError, OSError, KeyError, TypeError, subprocess.TimeoutExpired):
        report = {"status": "ERROR", "reason_codes": ["inspection_failed"],
                  "release_authorized": False, "read_only": True}
    if args.jsonl:
        print(json.dumps(report, sort_keys=True))
    else:
        print(f"{report['status']}: release runtime identity")
        for reason in report["reason_codes"]:
            print(" - " + reason)
        print("release_authorized=false")
    return {"PASS": 0, "DRIFT": 1, "ERROR": 2}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
