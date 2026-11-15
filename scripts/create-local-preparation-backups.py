#!/usr/bin/env python3
"""Create new private local Shelly preparation backups without stopping it."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

CONFIRM = "SINGLE_NVME_PREPARATION_BACKUP_ONLY"
VERIFIER = Path(__file__).with_name("verify-local-release-backups.py")
LABEL = "single_nvme_online_release_preparation"


class PreparationBlocked(RuntimeError):
    """Do not replace existing data or overstate snapshot readiness."""


def load_verifier() -> object:
    """Use the fixed adjacent independently tested immutable verifier."""
    spec = importlib.util.spec_from_file_location(
        "qa_local_preparation_verifier", VERIFIER
    )
    if spec is None or spec.loader is None:
        raise PreparationBlocked("VERIFIER_SOURCE_MISSING")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def source_connection(path: Path) -> sqlite3.Connection:
    """Open an existing SQLite source read-only; include committed WAL."""
    if not path.is_file() or path.is_symlink():
        raise PreparationBlocked("SOURCE_NOT_REGULAR")
    uri = path.resolve(strict=True).as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=15)


def space_guard(database: Path, root: Path) -> None:
    """Refuse backup when the local filesystem lacks ample capacity."""
    if not root.is_dir() or root.is_symlink():
        raise PreparationBlocked("OUTPUT_DIRECTORY_UNSAFE")
    if root.stat().st_uid != os.geteuid():
        raise PreparationBlocked("OUTPUT_DIRECTORY_NOT_OWNED")
    size = database.stat().st_size
    free = os.statvfs(root)
    if free.f_bavail * free.f_frsize < max(size * 8, 16 * 1024 * 1024):
        raise PreparationBlocked("INSUFFICIENT_LOCAL_DISK_SPACE")


def snapshot(source: sqlite3.Connection, dest: Path, verifier: object) -> dict:
    """Use SQLite online backup into one fsynced, atomic private file."""
    partial = dest.with_suffix(".partial")
    if partial.exists() or dest.exists():
        raise PreparationBlocked("DESTINATION_ALREADY_EXISTS")
    with closing(sqlite3.connect(partial)) as copy:
        source.backup(copy, pages=128, sleep=0.02)
    os.chmod(partial, 0o600)
    with partial.open("rb") as stream:
        os.fsync(stream.fileno())
    os.replace(partial, dest)
    return {
        "filename": dest.name,
        "sha256": verifier.hash_file(dest),
        "bytes": dest.stat().st_size,
        "state": verifier.sqlite_state(dest),
    }


def independent_restore(folder: Path, entry: dict, verifier: object) -> None:
    """Copy each snapshot and verify state on a separate immutable clone."""
    source = folder / entry["filename"]
    restored = folder / ("verify-" + entry["filename"])
    if restored.exists():
        raise PreparationBlocked("RESTORE_ALREADY_EXISTS")
    try:
        shutil.copyfile(source, restored)
        os.chmod(restored, 0o600)
        if verifier.hash_file(restored) != entry["sha256"]:
            raise PreparationBlocked("RESTORE_CHECKSUM_MISMATCH")
        if verifier.sqlite_state(restored) != entry["state"]:
            raise PreparationBlocked("RESTORE_STATE_MISMATCH")
    finally:
        if restored.exists():
            restored.unlink()


def write_manifest(folder: Path, entries: list[dict], image: str) -> None:
    """Bind separate snapshots to unsigned observed image provenance."""
    manifest = {
        "kind": LABEL,
        "kyiv_time": datetime.now(timezone.utc).isoformat(),
        "source_connection_mode": "read_only",
        "runtime_oci_ref_observed": image,
        "runtime_signature_reverified_in_this_run": False,
        "snapshots": entries,
        "local_independent_restore_checks": 2,
        "physical_failure_domains": 1,
        "single_disk_loss_survival": False,
        "last_cutover_snapshot": False,
        "release_approval": False,
    }
    partial = folder / "manifest.partial"
    with partial.open("x", encoding="utf-8") as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(manifest, stream, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(partial, folder / "manifest.json")


def build(database: Path, output_root: Path, name: str, image: str) -> dict:
    """Create only a brand-new preparation directory and two local copies."""
    verifier = load_verifier()
    if not re.fullmatch(r"release-prep-[a-z0-9-]{3,64}", name):
        raise PreparationBlocked("INVALID_NEW_BACKUP_NAME")
    if not re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", image):
        raise PreparationBlocked("INVALID_OBSERVED_OCI_DIGEST")
    if not database.is_file() or database.is_symlink():
        raise PreparationBlocked("SOURCE_NOT_REGULAR")
    space_guard(database, output_root)
    destination = output_root / name
    destination.mkdir(mode=0o700, exist_ok=False)
    os.chmod(destination, 0o700)
    with closing(source_connection(database)) as source:
        entries = [
            snapshot(source, destination / file, verifier)
            for file in ("current-A.sqlite3", "current-B.sqlite3")
        ]
    for entry in entries:
        independent_restore(destination, entry, verifier)
    write_manifest(destination, entries, image)
    report = verifier.verify(destination)
    if report["status"] != "PASS_LOCAL_COPY_READ_ONLY":
        raise PreparationBlocked("FINAL_IMMUTABLE_VERIFICATION_FAILED")
    return report


def main() -> int:
    """Default inert; never provide a release or final-cutover approval."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--database", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--name", default="")
    parser.add_argument("--image", default="")
    args = parser.parse_args()
    if not args.prepare:
        print("DRY_RUN: no backup directory or SQLite created")
        return 0
    if args.confirm != CONFIRM:
        print("BLOCKED: exact local preparation consent required")
        return 2
    if args.database is None or args.output_root is None:
        print("BLOCKED: database and output-root required")
        return 2
    try:
        result = build(args.database, args.output_root, args.name, args.image)
    except (PreparationBlocked, OSError, sqlite3.Error, ValueError) as exc:
        print("BACKUP_PREPARATION_BLOCKED", type(exc).__name__, str(exc)[:100])
        return 3
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
