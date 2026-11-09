"""Write and inspect a provenance-labelled local YASNO QA cache safely."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from yasno_schedule import (
    MAX_BYTES,
    YASNO_URL,
    fetch_live,
    outage_windows,
    parse_schedule,
    parse_timestamp,
)

CACHE_SCHEMA = 1
CACHE_LIMIT = MAX_BYTES + 8192
YASNO_REGION = 25
YASNO_DSO = 902


def canonical_data(data: object, limit: int = MAX_BYTES) -> bytes:
    """Serialize source data consistently for corruption detection."""

    if not isinstance(data, dict):
        raise ValueError("YASNO cache payload must be a JSON object")
    try:
        raw = json.dumps(
            data, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("YASNO cache payload is not JSON") from exc
    if len(raw) > limit:
        raise ValueError("YASNO cache payload exceeds the size limit")
    return raw


def build_cache(data: dict, captured: datetime) -> dict:
    """Describe a direct fetched response with explicit source metadata."""

    if captured.tzinfo is None or captured.utcoffset() is None:
        raise ValueError("capture time must be timezone-aware")
    checksum = hashlib.sha256(canonical_data(data)).hexdigest()
    return {
        "schema": CACHE_SCHEMA,
        "source_url": YASNO_URL,
        "region_id": YASNO_REGION,
        "dso_id": YASNO_DSO,
        "retrieved_at": captured.astimezone(timezone.utc).isoformat(),
        "sha256": checksum,
        "payload": data,
    }


def assert_safe_path(path: Path, writing: bool = False) -> None:
    """Reject links, unsafe existing files and missing parent folders."""

    if path.is_symlink() or any(
        parent.is_symlink() for parent in (path.parent, *path.parent.parents)
    ):
        raise ValueError("YASNO cache path may not be a symlink")
    if not path.parent.is_dir():
        raise ValueError("YASNO cache parent directory missing")
    if path.parent.stat().st_mode & 0o022:
        raise ValueError("YASNO cache parent is writable by other users")
    if path.exists():
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("YASNO cache path is not a regular file")
        if info.st_mode & 0o022:
            raise ValueError("YASNO cache is writable by another user")
    elif not writing:
        raise FileNotFoundError(path)


def atomic_write(path: str, envelope: dict) -> None:
    """Atomically install a 0600 cache file after validating its size."""

    target = Path(path)
    assert_safe_path(target, writing=True)
    raw = canonical_data(envelope, CACHE_LIMIT)
    if len(raw) > CACHE_LIMIT:
        raise ValueError("YASNO envelope too large")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=target.parent, prefix=".yasno-", delete=False
        ) as file:
            temporary = Path(file.name)
            os.fchmod(file.fileno(), 0o600)
            file.write(raw)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, target)
        descriptor = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def read_verified_cache(path: str) -> tuple[dict, datetime]:
    """Validate cache metadata and checksum; no provider calls."""

    file = Path(path)
    assert_safe_path(file)
    info = file.lstat()
    if info.st_size > CACHE_LIMIT:
        raise ValueError("YASNO trusted cache exceeds size limit")
    envelope = json.loads(file.read_bytes())
    if (
        not isinstance(envelope, dict)
        or envelope.get("schema") != CACHE_SCHEMA
    ):
        raise ValueError("YASNO trusted cache schema invalid")
    expected = (YASNO_URL, YASNO_REGION, YASNO_DSO)
    actual = tuple(
        envelope.get(key) for key in ("source_url", "region_id", "dso_id")
    )
    if actual != expected:
        raise ValueError("YASNO trusted cache provider metadata invalid")
    payload = envelope.get("payload")
    checksum = hashlib.sha256(canonical_data(payload)).hexdigest()
    if envelope.get("sha256") != checksum:
        raise ValueError("YASNO trusted cache checksum mismatch")
    captured = parse_timestamp(envelope.get("retrieved_at"))
    return payload, captured


def fetch_to_cache(path: str, group: str = "10.1"):
    """Validate the live direct response before replacing any old cache."""

    data, captured = fetch_live()
    result = parse_schedule(data, group, datetime.now(timezone.utc), captured)
    atomic_write(path, build_cache(data, captured))
    return result


def main() -> None:
    """Write an opt-in QA cache without touching Shelly or Telegram."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="Existing directory")
    parser.add_argument("--group", default="10.1")
    args = parser.parse_args()
    result = fetch_to_cache(args.output, args.group)
    print("YASNO cache updated (direct public HTTPS response)")
    print(f"Group {result.group} / day {result.day}")
    print(
        "Planned outage forecast:",
        ", ".join(outage_windows(result.bars)) or "none published",
    )
    print("No Shelly or Telegram changes")


if __name__ == "__main__":
    main()
