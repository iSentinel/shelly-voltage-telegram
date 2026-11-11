#!/usr/bin/env python3
"""Audit status identity and rollback archives without live modifications."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
import tarfile
import time
from pathlib import Path

MAX_SNAPSHOT_AGE_HOURS = 2


def parse_args() -> argparse.Namespace:
    """Require explicit source, database and independently copied snapshot."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--installed-version-file", required=True, type=Path)
    parser.add_argument("--chat-env", default="TELEGRAM_STATUS_CHAT_ID")
    parser.add_argument(
        "--owner-approval-file",
        type=Path,
        help="Optional protected Owner Approval receipt (read-only)",
    )
    parser.add_argument(
        "--jsonl",
        action="store_true",
        help="Emit exactly one machine-readable PASS/BLOCKED JSON object",
    )
    parser.add_argument(
        "--max-snapshot-age-hours",
        type=float,
        default=MAX_SNAPSHOT_AGE_HOURS,
    )
    return parser.parse_args()


def read_media_gate(database: Path, chat_env: str) -> list[str]:
    """Reuse the existing fail-closed, read-only Telegram status preflight."""

    if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", chat_env):
        return ["invalid channel environment name"]
    source = Path(__file__).with_name("graphic-migration-preflight.py")
    spec = importlib.util.spec_from_file_location("graphic_preflight", source)
    if spec is None or spec.loader is None:
        return ["migration preflight is unavailable"]
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        metadata, check = module.read_metadata(database)
    except ValueError:
        return ["SQLite metadata cannot be safely inspected"]
    return module.readiness_issues(metadata, check, os.getenv(chat_env, ""))


def safe_regular_file(path: Path) -> bool:
    """Reject links and paths with linked parents before accessing a file."""

    if any(part.is_symlink() for part in (path, *path.parents)):
        return False
    try:
        info = path.stat()
    except OSError:
        return False
    return stat.S_ISREG(info.st_mode) and info.st_nlink == 1


def snapshot_archives(snapshot: Path) -> tuple[Path, Path]:
    """Locate a source tarball and exactly one independent SQLite archive."""

    if snapshot.is_symlink() or not snapshot.is_dir():
        raise ValueError("snapshot directory missing or linked")
    if any(parent.is_symlink() for parent in snapshot.parents):
        raise ValueError("snapshot parent path is linked")
    archives = sorted(snapshot.glob("voltage-*.sqlite3.gz"))
    if len(archives) != 1:
        raise ValueError("snapshot must contain one SQLite archive")
    source = snapshot / "source.tar.gz"
    for archive in (source, archives[0]):
        if not safe_regular_file(archive):
            raise ValueError("snapshot archive missing or linked")
        if not safe_regular_file(Path(str(archive) + ".sha256")):
            raise ValueError("archive checksum missing or linked")
    return source, archives[0]


def verify_sha256(archive: Path) -> bool:
    """Require a checksum sidecar bound to the exact archived path."""

    try:
        sidecar = Path(str(archive) + ".sha256")
        if sidecar.stat().st_size > 512:
            return False
        manifest = sidecar.read_text()
        digest = hashlib.sha256()
        with archive.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return manifest == f"{digest.hexdigest()}  {archive}\n"
    except (OSError, ValueError):
        return False


def age_issues(archives: tuple[Path, Path], hours: float) -> list[str]:
    """Reject future-dated or stale archives before expensive verification."""

    if not 0 < hours <= 168:
        return ["invalid maximum snapshot age"]
    now = time.time()
    for file in archives:
        age = now - file.stat().st_mtime
        if age < -60 or age > hours * 3600:
            return ["rollback snapshot is stale or future-dated"]
    return []


def run_archive_validator(script: str, args: list[str]) -> bool:
    """Invoke the pinned source validator, suppressing private paths."""

    validator = Path(__file__).with_name(script)
    try:
        result = subprocess.run(
            [sys.executable, str(validator), *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=90,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def snapshot_issues(snapshot: Path, max_age: float) -> list[str]:
    """Validate independent checksums, freshness and archive contents."""

    try:
        archives = snapshot_archives(snapshot)
        problems = age_issues(archives, max_age)
        if problems:
            return problems
        if any(not verify_sha256(archive) for archive in archives):
            return ["rollback archive checksum mismatch"]
    except (OSError, ValueError):
        return ["rollback snapshot cannot be safely inspected"]
    source, sqlite = archives
    if not run_archive_validator("validate-source-archive.py", [str(source)]):
        return ["rollback source archive is invalid"]
    if not run_archive_validator(
        "validate-sqlite-archive.py", ["--archive", str(sqlite)]
    ):
        return ["rollback SQLite archive is invalid"]
    return []


def expected_version(path: Path) -> str | None:
    """Read a bounded, unlinked installed release identifier."""

    try:
        if not safe_regular_file(path) or path.stat().st_size > 128:
            return None
        value = path.read_bytes().decode("ascii").strip()
    except (OSError, UnicodeError):
        return None
    return (
        value
        if re.fullmatch(r"[0-9]+(?:\.[0-9]+){2}-[a-z0-9.-]+", value)
        else None
    )


def snapshot_version(archive: Path) -> str | None:
    """Read one source VERSION member only after archive validation."""

    try:
        with tarfile.open(archive, "r:gz") as source:
            matches = [
                item
                for item in source.getmembers()
                if item.name in {"VERSION", "./VERSION"}
            ]
            if len(matches) != 1 or matches[0].size > 128:
                return None
            handle = source.extractfile(matches[0])
            if handle is None:
                return None
            with handle:
                return handle.read(129).decode("ascii").strip()
    except (EOFError, OSError, UnicodeError, ValueError, tarfile.TarError):
        return None


def version_issues(snapshot: Path, installed: Path) -> list[str]:
    """Require the snapshot to restore exactly the installed release."""

    expected = expected_version(installed)
    if expected is None:
        return ["installed release VERSION file is invalid or linked"]
    actual = snapshot_version(snapshot / "source.tar.gz")
    if actual != expected:
        return ["rollback source VERSION differs from installed release"]
    return []


def owner_approval_verified(path: Path) -> bool:
    """Trust only a private, root-owned, single-use PR300 approval file."""

    if not safe_regular_file(path):
        return False
    try:
        info = path.stat()
        if (
            info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or not 0 < info.st_size <= 8192
        ):
            return False
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return False
    scope = record.get("approval_scope") if isinstance(record, dict) else None
    return bool(
        isinstance(scope, str)
        and "existing Shelly Telegram status" in scope
        and "PR300 graphic design" in scope
        and record.get("graphic_candidate_pr") == 300
        and record.get("owner_approval") == "GRANTED"
        and record.get("additional_owner_approval_required_for_same_scope")
        is False
        and record.get("no_automatic_force") is True
        and record.get("production_changed") is False
        and record.get("telegram_modified") is False
    )


def output_decision(
    issues: list[str], jsonl: bool, owner_approved: bool = False
) -> int:
    """Emit a safe outcome without exposing channel or message IDs."""

    allowed = not issues
    if jsonl:
        print(
            json.dumps(
                {
                    "ok": allowed,
                    "status": (
                        "READY_FOR_TECHNICAL_REVIEW"
                        if owner_approved
                        else "READY_FOR_OWNER_REVIEW"
                    )
                    if allowed
                    else "BLOCKED",
                    "deployment_allowed": False,
                    "owner_approval_required": not owner_approved,
                    "reasons": issues,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
    elif issues:
        print("GRAPHIC ROLLOUT: BLOCKED (read-only)")
        for issue in issues:
            print(f" - {issue}")
    elif owner_approved:
        print("GRAPHIC ROLLOUT: READY FOR TECHNICAL REVIEW (not deployment)")
        print("Owner Approval recorded; deployment remains separately gated.")
    else:
        print("GRAPHIC ROLLOUT: READY FOR OWNER REVIEW (not deployment)")
        print("Production activation still requires separate Owner Approval.")
    return 0 if allowed else 2


def main() -> int:
    """Print a private-identifier-free decision; never activate graphics."""

    args = parse_args()
    owner_approved = False
    if args.owner_approval_file is not None:
        owner_approved = owner_approval_verified(args.owner_approval_file)
    issues = read_media_gate(args.database, args.chat_env)
    if args.owner_approval_file is not None and not owner_approved:
        issues.append("owner approval evidence is invalid")
    snapshots = snapshot_issues(args.snapshot, args.max_snapshot_age_hours)
    issues.extend(snapshots)
    if not snapshots:
        issues.extend(
            version_issues(args.snapshot, args.installed_version_file)
        )
    return output_decision(issues, args.jsonl, owner_approved)


if __name__ == "__main__":
    raise SystemExit(main())
