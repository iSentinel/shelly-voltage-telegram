#!/usr/bin/env python3
"""Check published releases and enforce safe update prerequisites."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import urllib.request
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

REPO = "iSentinel/shelly-voltage-telegram"
IMAGE = "ghcr.io/isentinel/shelly-voltage-telegram"
API = f"https://api.github.com/repos/{REPO}/releases"
DEFAULT_STATE = Path("/var/lib/shelly-voltage-update-check")
ABSENT_CREDENTIAL = "shelly-version-not-provisioned"
VERSION_RE = re.compile(r"v?(\d+)\.(\d+)\.(\d+)-hw\Z")
REQUEST_TIMEOUT = 12
MAX_RESPONSE_BYTES = 128 * 1024
SAFE_VOLTAGE_MIN = 207.0
SAFE_VOLTAGE_MAX = 253.0
MAX_SAMPLE_AGE = 120
MIN_RECOVERY_STABLE_SECONDS = 15 * 60
PHASE_ORDER = ("L1", "L2", "L3")
TELEGRAM_PENDING_KEYS = (
    "telegram_graphic_publish_pending",
    "telegram_legacy_text_confirm_pending",
    "telegram_recovery_rotation_pending_at",
)
MIN_UPDATE_SPACE_BYTES = 768 * 1024 * 1024


def version_tuple(text: str) -> tuple[int, int, int]:
    """Parse a supported release version without accepting arbitrary tags."""

    match = VERSION_RE.fullmatch(text.strip())
    if match is None:
        raise ValueError("unsupported release version")
    return tuple(int(part) for part in match.groups())


def decode_json_response(reply) -> dict:
    """Reject oversized, incomplete or unexpected API JSON data."""

    payload = reply.read(MAX_RESPONSE_BYTES + 1)
    if len(payload) > MAX_RESPONSE_BYTES:
        raise ValueError("API response exceeds 128 KiB")
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError("API response must be a JSON object")
    return data


def request_json(url: str, headers: dict | None = None) -> dict:
    """Fetch a bounded HTTPS JSON response with a fixed timeout."""

    if not url.startswith("https://"):
        raise ValueError("only HTTPS release APIs are allowed")
    head = {"User-Agent": "shelly-voltage-updater/1"}
    head.update(headers or {})
    with urllib.request.urlopen(
        urllib.request.Request(url, headers=head), timeout=REQUEST_TIMEOUT
    ) as reply:
        return decode_json_response(reply)


def published_release(tag: str | None = None) -> dict:
    """Require a published, non-prerelease GitHub release."""

    url = f"{API}/tags/{tag}" if tag else f"{API}/latest"
    release = request_json(url)
    if not isinstance(release, dict):
        raise ValueError("invalid GitHub release metadata")
    if release.get("draft") or release.get("prerelease"):
        raise ValueError("draft or prerelease is not eligible")
    actual = release.get("tag_name")
    if not isinstance(actual, str) or not actual.startswith("v"):
        raise ValueError("stable release tag is missing")
    version_tuple(actual)
    if tag and actual != tag:
        raise ValueError("release tag mismatch")
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise ValueError("release assets must be a list")
    names = {
        item["name"]
        for item in assets
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    required = {f"shelly-voltage-telegram-{actual[1:]}.tar.gz", "SHA256SUMS"}
    if not required.issubset(names):
        raise ValueError("source archive and checksums are required")
    return release


def version_input(path: Path, absent: str | None) -> str | None:
    """Read a version input, including an optional systemd fallback."""
    if not path.is_file():
        return None
    value = path.read_text(encoding="utf-8").strip()
    return None if value == absent else value


def installed_version(
    root: Path, state: Path, *, absent: str | None = None
) -> str:
    """Prefer deployed or published release to candidate source version."""

    deployed = version_input(state / "deployed.json", absent)
    if deployed is not None:
        version = json.loads(deployed)["version"]
    else:
        version = version_input(root / "RELEASE_VERSION", absent)
        if version is None:
            version = (root / "VERSION").read_text(encoding="utf-8").strip()
    version_tuple(version)
    return version.removeprefix("v")


def atomic_json(path: Path, payload: dict) -> None:
    """Replace a small status file without exposing partial content."""

    path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temp = Path(stream.name)
    os.chmod(temp, 0o640)
    os.replace(temp, path)


def check(root: Path, state: Path, credentials: Path | None = None) -> dict:
    """Return release availability; never install anything."""

    if credentials is None:
        current = installed_version(root, state)
    else:
        current = installed_version(
            credentials, credentials, absent=ABSENT_CREDENTIAL
        )
    release = published_release()
    latest = release["tag_name"].removeprefix("v")
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "installed": current,
        "latest": latest,
        "available": version_tuple(latest) > version_tuple(current),
        "release_url": f"https://github.com/{REPO}/releases/tag/v{latest}",
        "auto_install": False,
    }


def guard_target(root: Path, state: Path, version: str) -> None:
    """Refuse reinstall, downgrade, or unpublished update targets."""

    target = version_tuple(version)
    current = version_tuple(installed_version(root, state))
    if target <= current:
        raise ValueError("target must be newer than the deployed version")
    published_release("v" + version.removeprefix("v"))


def manifest_digest(tag: str) -> str:
    """Resolve the published tag to the immutable GHCR index digest."""

    scope = "repository:isentinel/shelly-voltage-telegram:pull"
    token_url = f"https://ghcr.io/token?service=ghcr.io&scope={scope}"
    token = request_json(token_url)["token"]
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.oci.image.index.v1+json",
    }
    url = (
        f"https://ghcr.io/v2/isentinel/shelly-voltage-telegram/manifests/{tag}"
    )
    with urllib.request.urlopen(
        urllib.request.Request(url, headers=headers), timeout=REQUEST_TIMEOUT
    ) as reply:
        media_type = reply.headers.get("Content-Type", "")
        digest = reply.headers.get("Docker-Content-Digest", "")
        manifest = decode_json_response(reply)
    if "image.index" not in media_type:
        raise ValueError("expected a signed multi-platform OCI index")
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
        raise ValueError("registry did not return a valid digest")
    manifests = manifest.get("manifests")
    if not isinstance(manifests, list) or not manifests:
        raise ValueError("OCI index has no platform manifests")
    return digest


def ready_phases(status: dict) -> tuple[str, ...]:
    """Require a ready entry for every phase advertised by the monitor."""
    if "phases" not in status:
        return ()  # Older readiness endpoints expose a single aggregate.
    phases = status["phases"]
    if (
        not isinstance(phases, dict)
        or "L1" not in phases
        or set(phases) - set(PHASE_ORDER)
        or status.get("unready_phases")
    ):
        raise ValueError("invalid or incomplete phase readiness")
    for health in phases.values():
        if (
            not isinstance(health, dict)
            or health.get("ready") is not True
            or health.get("connectivity") != "online"
        ):
            raise ValueError("a configured phase is offline or stale")
    return tuple(phase for phase in PHASE_ORDER if phase in phases)


def verify_voltage_samples(
    db: sqlite3.Connection, phases: tuple[str, ...], now: float
) -> None:
    """Check each configured phase against freshness and voltage limits."""
    for phase in phases or (None,):
        condition, parameters = (
            (" WHERE phase = ?", (phase,)) if phase else ("", ())
        )
        sample = db.execute(
            "SELECT ts, voltage FROM measurements"
            + condition
            + " ORDER BY ts DESC LIMIT 1",
            parameters,
        ).fetchone()
        if not sample or not 0 <= now - sample[0] <= MAX_SAMPLE_AGE:
            raise ValueError("voltage sample is missing or stale")
        if not SAFE_VOLTAGE_MIN <= sample[1] <= SAFE_VOLTAGE_MAX:
            raise ValueError("measured voltage is outside the upgrade band")


def recovery_metadata(db: sqlite3.Connection, phases: tuple[str, ...]) -> dict:
    """Read only the fields needed for an update decision."""
    keys = (
        "voltage_episode",
        "last_voltage_event",
        "voltage_recovery_stable_since",
        *TELEGRAM_PENDING_KEYS,
        *(f"voltage_detector_{phase}" for phase in phases),
    )
    placeholders = ",".join("?" for _ in keys)
    return dict(
        db.execute(
            f"SELECT key, value FROM meta WHERE key IN ({placeholders})", keys
        )
    )


def read_meta_object(value: str | None) -> dict:
    """Read one recorded object, leaving malformed state unconfirmed."""
    try:
        data = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def timestamp(value: object) -> float:
    """Reject missing, non-finite or non-positive timestamps."""
    try:
        point = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("invalid recovery timestamp") from exc
    if isinstance(value, bool) or not math.isfinite(point) or point <= 0:
        raise ValueError("invalid recovery timestamp")
    return point


def verify_recovery(meta: dict, phases: tuple[str, ...], now: float) -> None:
    """Require a finalized recovery and corroborating normal detectors."""
    raw_episode = meta.get("voltage_episode")
    stable = meta.get("voltage_recovery_stable_since")
    if not raw_episode and not stable:
        return
    episode = read_meta_object(raw_episode)
    event = read_meta_object(meta.get("last_voltage_event"))
    transitions = episode.get("transitions")
    if raw_episode and (
        event.get("kind") != "recovered"
        or not isinstance(transitions, list)
        or not transitions
        or not isinstance(transitions[-1], dict)
        or transitions[-1].get("kind") != "recovered"
    ):
        raise ValueError("a voltage alarm episode is active")
    try:
        since = timestamp(stable)
        event_at = timestamp(event.get("timestamp"))
        last_at = timestamp(episode.get("last_at"))
        finalized_at = timestamp(episode.get("finalized_at"))
    except ValueError as exc:
        raise ValueError("voltage recovery is not yet stable") from exc
    if (
        not phases
        or event.get("kind") != "recovered"
        or event.get("phase") not in phases
        or not event_at
        == last_at
        <= since
        <= now - MIN_RECOVERY_STABLE_SECONDS
        or not last_at <= finalized_at <= now
    ):
        raise ValueError("voltage recovery is not yet stable")
    verify_phase_detectors(meta, phases)


def verify_phase_detectors(meta: dict, phases: tuple[str, ...]) -> None:
    """Require normal confirmation on every phase after a logged recovery."""
    for phase in phases:
        state = read_meta_object(meta.get(f"voltage_detector_{phase}"))
        if state.get("level") != "normal" or state.get("low") is not False:
            raise ValueError(
                "a phase detector has not confirmed normal voltage"
            )


def preflight(database: Path, ready_url: str) -> None:
    """Refuse an update during an outage, stale data or voltage alarm."""

    if ready_url != "http://127.0.0.1:8080/ready":
        raise ValueError("only the local readiness endpoint is allowed")
    with urllib.request.urlopen(ready_url, timeout=5) as response:
        status = decode_json_response(response)
    if (
        status.get("ready") is not True
        or status.get("connectivity") != "online"
    ):
        raise ValueError("Shelly is offline or readings are not fresh")
    phases = ready_phases(status)
    if not database.is_file():
        raise ValueError("SQLite database is missing")
    uri = f"file:{database.resolve()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as db:
        db.execute("BEGIN")
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("SQLite integrity check failed")
        open_count = db.execute(
            "SELECT COUNT(*) FROM outages WHERE ended_at IS NULL"
        ).fetchone()[0]
        if open_count:
            raise ValueError("an electrical or connectivity outage is active")
        now = time.time()
        meta = recovery_metadata(db, phases)
        verify_recovery(meta, phases, now)
        verify_voltage_samples(db, phases, now)
        if any(key in meta for key in TELEGRAM_PENDING_KEYS):
            raise ValueError("Telegram status publication is unresolved")
        if db.execute(
            "SELECT 1 FROM meta WHERE key GLOB 'telegram_report_pending:*' "
            "LIMIT 1"
        ).fetchone():
            raise ValueError("Telegram report delivery is unresolved")


def storage_preflight(database: Path, state: Path, docker_root: Path) -> None:
    """Require space for the image, backup and recovery snapshot."""

    if not database.is_file():
        raise ValueError("SQLite database is missing")
    required = max(
        MIN_UPDATE_SPACE_BYTES,
        database.stat().st_size * 3 + 256 * 1024 * 1024,
    )
    for name, directory in (
        ("backup", state),
        ("Docker", docker_root),
        ("temporary files", Path(tempfile.gettempdir())),
    ):
        if not directory.is_dir():
            raise ValueError(f"{name} filesystem is unavailable")
        free = shutil.disk_usage(directory).free
        if free < required:
            raise ValueError(
                f"insufficient {name} disk space: "
                f"{free // 1048576} MiB free, "
                f"{required // 1048576} MiB required"
            )


def parser() -> argparse.ArgumentParser:
    """Define the minimal, non-interactive updater CLI."""

    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument(
        "action", choices=("check", "preflight", "digest", "guard", "storage")
    )
    command.add_argument(
        "--root", type=Path, default=Path("/opt/shelly-voltage")
    )
    command.add_argument("--state", type=Path, default=DEFAULT_STATE)
    command.add_argument(
        "--credentials",
        type=Path,
        help="Read version files copied by systemd for the scheduled check.",
    )
    command.add_argument(
        "--status-file",
        type=Path,
        help="Write the check result here instead of STATE/status.json.",
    )
    command.add_argument(
        "--database",
        type=Path,
        default=Path("/var/lib/shelly-voltage/voltage.sqlite3"),
    )
    command.add_argument("--docker-root", type=Path)
    command.add_argument("--version", default="")
    return command


def main() -> int:
    """Execute exactly one check and return a stable exit status."""

    args = parser().parse_args()
    try:
        if args.action == "check":
            result = check(args.root, args.state, args.credentials)
            atomic_json(args.status_file or args.state / "status.json", result)
            print(json.dumps(result, sort_keys=True))
        elif args.action == "preflight":
            preflight(args.database, "http://127.0.0.1:8080/ready")
            print("PREFLIGHT PASS")
        elif args.action == "storage":
            if args.docker_root is None:
                raise ValueError("--docker-root is required for storage")
            storage_preflight(args.database, args.state, args.docker_root)
            print("STORAGE PASS")
        elif args.action == "guard":
            guard_target(args.root, args.state, args.version)
            print("TARGET PASS")
        else:
            tag = "v" + args.version.removeprefix("v")
            version_tuple(tag)
            published_release(tag)
            print(manifest_digest(tag))
        return 0
    except (OSError, ValueError, KeyError, sqlite3.Error) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
