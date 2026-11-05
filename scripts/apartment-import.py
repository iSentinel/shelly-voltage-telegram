#!/usr/bin/env python3
"""Preview or atomically import apartment-to-phase CSV assignments."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apartment_import import (
    MAX_CSV_BYTES,
    import_summary,
    parse_apartment_csv,
)
from apartment_phases import ApartmentRegistry, valid_phase


def parse_args() -> argparse.Namespace:
    """Read explicit file paths and optional apply confirmation."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--phases", default="L1")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply all assignments in a single SQLite transaction.",
    )
    return parser.parse_args()


def main() -> int:
    """Validate a file before touching an existing monitor database."""

    args = parse_args()
    try:
        if args.database.is_symlink() or not args.database.is_file():
            raise ValueError("database must be an existing regular file")
        if args.csv.is_symlink() or not args.csv.is_file():
            raise ValueError("CSV must be an existing regular file")
        if args.csv.stat().st_size > MAX_CSV_BYTES:
            raise ValueError("CSV exceeds 256 KiB")
        phases = {
            valid_phase(value.strip()) for value in args.phases.split(",")
        }
        entries = parse_apartment_csv(args.csv.read_bytes(), phases)
        print(import_summary(entries))
        if not args.apply:
            print("PASS: preview only; no database changes")
            return 0
        registry = ApartmentRegistry(args.database)
        registry.initialize()
        count = registry.import_entries(entries)
        print(f"PASS: saved {count} apartment assignments atomically")
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
