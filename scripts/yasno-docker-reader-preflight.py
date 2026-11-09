"""Read-only Docker acceptance gate for the isolated YASNO Telegram reader."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from yasno_cache import read_verified_cache  # noqa: E402
from yasno_handoff import validate_target  # noqa: E402
from yasno_schedule import parse_schedule  # noqa: E402

EXPECTED_SOURCE = "/var/lib/shelly-yasno-reader"
EXPECTED_TARGET = "/run/yasno"
EXPECTED_CACHE = "/run/yasno/yasno.json"
EXPECTED_UID = "10001:10001"
EXPECTED_GROUP = "10.1"


def docker_inspect(name: str) -> dict:
    """Read one local Docker object without printing secret environment."""

    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", name):
        raise ValueError("invalid Docker container name")
    try:
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{json .}}", name],
            capture_output=True,
            text=True,
            check=False,
            timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("Docker inspection unavailable") from exc
    if result.returncode != 0 or len(result.stdout) > 1048576:
        raise ValueError("Docker inspect failed or was oversized")
    return json.loads(result.stdout)


def restricted_env(config: dict) -> dict[str, str]:
    """Extract only nonsecret rollout flags from Docker environment."""

    keys = {
        "TELEGRAM_YASNO_STATUS_ENABLED",
        "YASNO_TRUSTED_CACHE_FILE",
        "YASNO_GROUP",
    }
    values: dict[str, str] = {}
    for entry in config.get("Env", []):
        if not isinstance(entry, str) or "=" not in entry:
            raise ValueError("malformed Docker environment")
        key, value = entry.split("=", 1)
        if key in keys:
            if key in values:
                raise ValueError("duplicate YASNO flag")
            values[key] = value
    return values


def reader_mount(document: dict) -> dict:
    """Require one readonly reader bind with no overlapping mounts."""

    mounts = document.get("Mounts")
    if not isinstance(mounts, list):
        raise ValueError("YASNO Docker mount list unavailable")
    matched = []
    for mount in mounts:
        if not isinstance(mount, dict):
            raise ValueError("malformed Docker mount")
        destination = mount.get("Destination")
        if not isinstance(destination, str) or not destination.startswith("/"):
            raise ValueError("invalid Docker mount destination")
        if any(part in {"", ".", ".."} for part in destination.split("/")[1:]):
            raise ValueError("noncanonical Docker mount destination")
        if destination == EXPECTED_TARGET:
            matched.append(mount)
        elif destination.startswith(
            EXPECTED_TARGET + "/"
        ) or EXPECTED_TARGET.startswith(destination.rstrip("/") + "/"):
            raise ValueError("overlapping YASNO reader mount")
    if len(matched) != 1:
        raise ValueError("YASNO reader mount missing or duplicated")
    mount = matched[0]
    if (
        mount.get("Type") != "bind"
        or mount.get("Source") != EXPECTED_SOURCE
        or mount.get("RW") is not False
    ):
        raise ValueError("YASNO mount source or read-only flag invalid")
    return mount


def secure_container(document: dict) -> dict[str, str]:
    """Require a running nonprivileged reader with read-only Docker."""

    state = document.get("State")
    if not isinstance(state, dict):
        raise ValueError("YASNO reader Docker state missing")
    if state.get("Running") is not True or any(
        state.get(key) is not False for key in ("Paused", "Restarting", "Dead")
    ):
        raise ValueError("YASNO reader is not running normally")
    config = document["Config"]
    host = document["HostConfig"]
    dropped = host.get("CapDrop") or []
    if (
        config.get("User") != EXPECTED_UID
        or host.get("ReadonlyRootfs") is not True
        or host.get("Privileged") is not False
        or "ALL" not in dropped
    ):
        raise ValueError("Docker user or isolation policy invalid")
    return restricted_env(config)


def verify_reader(document: dict, phase: str) -> str:
    """Distinguish staged-but-disabled mount from fully active cache."""

    if phase not in {"staging", "active"}:
        raise ValueError("invalid YASNO rollout phase")
    flags = secure_container(document)
    reader_mount(document)
    if flags.get("YASNO_TRUSTED_CACHE_FILE") != EXPECTED_CACHE:
        raise ValueError("trusted cache target differs from reader mount")
    if flags.get("YASNO_GROUP") != EXPECTED_GROUP:
        raise ValueError("YASNO group is not approved")
    enabled = "0" if phase == "staging" else "1"
    if flags.get("TELEGRAM_YASNO_STATUS_ENABLED") != enabled:
        raise ValueError("YASNO Telegram feature phase mismatch")
    if phase == "active":
        target = Path(EXPECTED_SOURCE) / "yasno.json"
        validate_target(target, 10001)
        cache, captured = read_verified_cache(str(target))
        parse_schedule(
            cache, EXPECTED_GROUP, datetime.now(timezone.utc), captured
        )
    return f"phase={phase} secure_mount=true feature_enabled={enabled}"


def main() -> int:
    """Read-only host preflight; never install or restart Docker."""

    parser = argparse.ArgumentParser(description=__doc__)
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument("--dry-run", action="store_true")
    operation.add_argument("--check", action="store_true")
    parser.add_argument(
        "--phase", choices=("staging", "active"), default="active"
    )
    parser.add_argument("--container", default="shelly-voltage-bot")
    parser.add_argument("--inspect-fixture", type=Path, default=None)
    args = parser.parse_args()
    if args.dry_run:
        print("PASS DRY_RUN no_docker_access=true no_writes=true")
        return 0
    try:
        document = (
            json.loads(args.inspect_fixture.read_text(encoding="utf-8"))
            if args.inspect_fixture
            else docker_inspect(args.container)
        )
        print("PASS YASNO_DOCKER", verify_reader(document, args.phase))
        return 0
    except (ValueError, OSError, TypeError, KeyError, AttributeError):
        print("FAIL YASNO_DOCKER validation_failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
