"""Validate small apartment CSV batches before any database changes."""

from __future__ import annotations

import csv
import io
from collections import Counter

from apartment_phases import (
    valid_apartment,
    valid_building,
    valid_location_number,
    valid_phase,
)

COLUMNS = ("building", "apartment", "phase", "entrance", "floor")
MAX_CSV_BYTES = 262144
MAX_CSV_ROWS = 2000
ApartmentRow = tuple[str, int, str, int | None, int | None]


def parse_entry(record: dict[str, str], allowed: set[str]) -> ApartmentRow:
    """Validate a complete CSV row and its optional apartment location."""

    if set(record) != set(COLUMNS):
        raise ValueError("CSV row has unexpected or missing columns")
    if any(not isinstance(record[name], str) for name in COLUMNS):
        raise ValueError("CSV row has missing values")
    building = valid_building(record["building"])
    number = valid_apartment(record["apartment"])
    phase = valid_phase(record["phase"])
    if phase not in allowed:
        raise ValueError("CSV phase is not configured on this monitor")
    entrance, floor = record["entrance"], record["floor"]
    if bool(entrance) != bool(floor):
        raise ValueError("entrance and floor must both be set or both empty")
    if not entrance:
        return building, number, phase, None, None
    entry = valid_location_number(entrance, "entrance", 1, 100)
    level = valid_location_number(floor, "floor", 0, 200)
    return building, number, phase, entry, level


def parse_apartment_csv(raw: bytes, allowed: set[str]) -> list[ApartmentRow]:
    """Read a bounded UTF-8 CSV and reject every invalid batch atomically."""

    if len(raw) > MAX_CSV_BYTES:
        raise ValueError("CSV exceeds 256 KiB")
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeError as exc:
        raise ValueError("CSV must use UTF-8") from exc
    reader = csv.DictReader(io.StringIO(content, newline=""), strict=True)
    if reader.fieldnames != list(COLUMNS):
        raise ValueError("CSV header must match building,apartment,phase,entrance,floor")
    normalized = {valid_phase(phase) for phase in allowed}
    seen: set[tuple[str, int]] = set()
    entries: list[ApartmentRow] = []
    try:
        for record in reader:
            if len(entries) >= MAX_CSV_ROWS:
                raise ValueError("CSV exceeds 2000 apartments")
            entry = parse_entry(record, normalized)
            identifier = entry[:2]
            if identifier in seen:
                raise ValueError("CSV contains duplicate apartments")
            seen.add(identifier)
            entries.append(entry)
    except csv.Error as exc:
        raise ValueError("CSV has malformed quoting") from exc
    if not entries:
        raise ValueError("CSV has no apartments")
    return entries


def import_summary(entries: list[ApartmentRow]) -> str:
    """Show a summary without exposing apartment numbers."""

    counts = Counter((building, phase) for building, _, phase, _, _ in entries)
    lines = [f"Validated apartments: {len(entries)}"]
    lines.extend(
        f"{building} {phase}: {count}"
        for (building, phase), count in sorted(counts.items())
    )
    return "\n".join(lines)
