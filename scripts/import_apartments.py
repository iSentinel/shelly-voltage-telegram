#!/usr/bin/env python3
"""Validate or import apartment assignments from a CSV file."""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apartment_phases import (  # noqa: E402
    ApartmentRegistry,
    valid_apartment,
    valid_building,
    valid_location_number,
    valid_phase,
)

COLUMNS = {"building", "apartment", "phase", "entrance", "floor"}
MAX_ROWS = 1000


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse an explicit dry-run or apply request."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, type=Path)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument(
        "--allowed-phases", default="L1", help="Comma-separated active phases"
    )
    parser.add_argument(
        "--apply", action="store_true", help="Write changes after validation"
    )
    return parser.parse_args(argv)


def parse_row(row: dict, allowed: set[str]) -> tuple:
    """Validate and normalize a single CSV assignment."""

    building = valid_building(row["building"].strip())
    apartment = valid_apartment(row["apartment"].strip())
    phase = valid_phase(row["phase"].strip())
    if phase not in allowed:
        raise ValueError("phase is not configured")
    entry, floor = row["entrance"].strip(), row["floor"].strip()
    if bool(entry) != bool(floor):
        raise ValueError("entrance and floor must be provided together")
    entrance = (
        valid_location_number(entry, "entrance", 1, 100) if entry else None
    )
    level = valid_location_number(floor, "floor", 0, 200) if floor else None
    return (building, apartment, phase, entrance, level)


def read_rows(path: Path, allowed: set[str]) -> list[tuple]:
    """Validate the complete file before opening a writable database."""

    records: list[tuple] = []
    seen: set[tuple[str, int]] = set()
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if set(reader.fieldnames or ()) != COLUMNS:
            raise ValueError(
                "CSV columns must be building,apartment,phase,entrance,floor"
            )
        for number, row in enumerate(reader, 2):
            if len(records) >= MAX_ROWS:
                raise ValueError("CSV exceeds the 1000-row limit")
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"line {number}: invalid column count")
            try:
                entry = parse_row(row, allowed)
            except ValueError as exc:
                raise ValueError(f"line {number}: {exc}") from None
            key = entry[:2]
            if key in seen:
                raise ValueError(f"line {number}: duplicate apartment")
            records.append(entry)
            seen.add(key)
    if not records:
        raise ValueError("CSV has no apartment assignments")
    return records


def save_rows(db_path: Path, rows: list[tuple]) -> None:
    """Commit the validated assignments as one SQLite transaction."""

    if not db_path.is_file():
        raise ValueError("database must already exist")
    registry = ApartmentRegistry(db_path)
    registry.initialize()
    with closing(registry.connect()) as db, db:
        db.executemany(
            "INSERT INTO apartment_phases "
            "(building,apartment,phase,entrance,floor) VALUES(?,?,?,?,?) "
            "ON CONFLICT(building,apartment) DO UPDATE SET "
            "phase=excluded.phase, "
            "entrance=COALESCE(excluded.entrance,apartment_phases.entrance), "
            "floor=COALESCE(excluded.floor,apartment_phases.floor)",
            rows,
        )


def main(argv: list[str] | None = None) -> int:
    """Validate input and optionally apply it with clear exit codes."""

    args = parse_args(argv)
    try:
        allowed = {
            valid_phase(x.strip()) for x in args.allowed_phases.split(",")
        }
        rows = read_rows(args.csv, allowed)
        if args.apply:
            save_rows(args.db, rows)
        print(
            f"PASS: {len(rows)} assignments "
            f"{'applied' if args.apply else 'validated (dry-run)'}"
        )
        return 0
    except (ValueError, OSError, sqlite3.Error, csv.Error) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
