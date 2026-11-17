#!/usr/bin/env python3
"""Inventory restorable SQLite backup families without authorizing releases."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import time
from pathlib import Path

FAMILIES = frozenset({"periodic", "cutover", "updater", "source_upgrade"})
ROOT = Path(__file__).resolve().parents[1]


def load_guard():
    """Load the existing strict whole-history parity implementation."""

    src = ROOT / "scripts/em50-predeploy-backup-guard.py"
    spec = importlib.util.spec_from_file_location("em50_family_guard", src)
    if spec is None or spec.loader is None:
        raise ValueError("strict SQLite guard unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_families(values: list[str]) -> list[tuple[str, Path]]:
    """Accept explicit known families and refuse ambiguous identifiers."""

    result = []
    seen = set()
    for value in values:
        family, separator, directory = value.partition("=")
        if not separator or family not in FAMILIES or not directory:
            raise ValueError("invalid backup family specification")
        if family in seen:
            raise ValueError("duplicate backup family")
        seen.add(family)
        path = Path(directory)
        if not path.is_absolute():
            raise ValueError("backup directory must be absolute")
        if any(item.is_symlink() for item in (path, *path.parents)):
            raise ValueError("backup directory contains a symlink")
        result.append((family, path))
    return result


def inspect_family(guard, family, directory, live, now, max_age):
    """Validate one archived family and compare to a live read snapshot."""

    archive = guard.newest_backup(directory)
    guard.verify_checksum(archive)
    saved = guard.unpack_state(archive)
    age = (now - archive.stat().st_mtime) / 60
    reasons, measurements_match, outages_match = guard.parity_reasons(
        live, saved, age, max_age
    )
    return {
        "family": family,
        "status": "RESTORED_VALID",
        "archive_filename": archive.name,
        "sha256": "PASS",
        "sqlite_restore": "PASS",
        "backup_age_minutes": round(age, 2),
        "saved_measurements": saved["rows"],
        "live_measurements": live["rows"],
        "record_gap": live["rows"] - saved["rows"],
        "current_parity": "FAIL" if reasons else "PASS",
        "parity_reason_codes": reasons,
        "measurements_match": measurements_match,
        "outages_match": outages_match,
        "whole_sqlite_state_matches": (
            saved["whole_digest"] == live["whole_digest"]
        ),
        "saved_status_metadata_present": bool(saved["status"]),
        "writer_fenced": "NOT_ATTESTED_BY_INVENTORY",
    }


def inventory(db, entries, max_age, now):
    """Return archive facts without giving permission for a new cutover."""

    if not 1 <= max_age <= 120:
        raise ValueError("maximum age outside strict release limits")
    if db.is_symlink() or not db.is_file():
        raise ValueError("live SQLite missing or symlinked")
    guard = load_guard()
    live = guard.sqlite_state(db)
    families = []
    for family, directory in entries:
        try:
            result = inspect_family(
                guard, family, directory, live, now, max_age
            )
        except (
            OSError,
            ValueError,
            TypeError,
            sqlite3.Error,
            EOFError,
        ) as exc:
            result = {
                "family": family,
                "status": "ARCHIVE_INVALID",
                "error_type": type(exc).__name__,
                "current_parity": "NOT_CHECKED",
                "writer_fenced": "NOT_ATTESTED_BY_INVENTORY",
            }
        families.append(result)
    return inventory_result(live, families)


def inventory_result(live, families):
    """Never conflate historical archive integrity with release approval."""

    good = all(item["status"] == "RESTORED_VALID" for item in families)
    periodic = next(
        (item for item in families if item["family"] == "periodic"), None
    )
    return {
        "kind": "em50-backup-family-inventory",
        "status": "PASS_ARCHIVES_ONLY" if good else "FAIL_ARCHIVE_INTEGRITY",
        "source_live_read_only": True,
        "archives_restored_in_disposable_locations": True,
        "live_measurements": live["rows"],
        "families": families,
        "periodic_predeploy_parity": (
            periodic["current_parity"] if periodic else "NOT_CHECKED"
        ),
        "source_offhost_independence_verified": False,
        "fresh_writer_fenced_backup_created": False,
        "release_authorized": False,
        "production_modified": False,
    }


def options() -> argparse.Namespace:
    """Parse explicit locations, never silently assume release authority."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("/var/lib/shelly-voltage/voltage.sqlite3"),
    )
    parser.add_argument(
        "--snapshot", action="append", required=True, metavar="FAMILY=DIR"
    )
    parser.add_argument("--max-age-minutes", type=float, default=30)
    parser.add_argument("--jsonl", action="store_true")
    return parser.parse_args()


def main() -> int:
    """Return success for inventory integrity only, not cutover readiness."""

    args = options()
    try:
        entries = parse_families(args.snapshot)
        result = inventory(args.db, entries, args.max_age_minutes, time.time())
    except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
        result = {
            "status": "FAIL_ARCHIVE_INTEGRITY",
            "reason": type(exc).__name__,
            "release_authorized": False,
            "production_modified": False,
        }
    if args.jsonl:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print(result["status"], "release_authorized=false")
    return 0 if result["status"] == "PASS_ARCHIVES_ONLY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
