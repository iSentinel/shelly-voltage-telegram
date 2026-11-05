"""Store apartment-to-phase assignments without controlling power."""

from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from pathlib import Path

BUILDING_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,32}\Z")
PHASES = {"L1", "L2", "L3"}
MAX_APARTMENT = 99999
MAX_LIST = 100


def valid_building(name: str) -> str:
    """Validate a compact building identifier used in Telegram commands."""

    if not BUILDING_PATTERN.fullmatch(name):
        raise ValueError("building must be 1-32 letters, digits, _ or -")
    return name


def valid_apartment(value: str) -> int:
    """Validate an apartment number."""

    if not value.isdecimal():
        raise ValueError("apartment must be a positive number")
    number = int(value)
    if not 1 <= number <= MAX_APARTMENT:
        raise ValueError("apartment must be between 1 and 99999")
    return number


def valid_phase(value: str) -> str:
    """Normalize the physical phase name."""

    phase = value.upper()
    if phase == "L":
        phase = "L1"
    if phase not in PHASES:
        raise ValueError("phase must be L1, L2 or L3")
    return phase


def valid_location_number(
    value: str, name: str, minimum: int, maximum: int
) -> int:
    """Validate one entrance or floor number."""

    if not value.isdecimal():
        raise ValueError(f"{name} must be a whole number")
    number = int(value)
    if not minimum <= number <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return number


class ApartmentRegistry:
    """Manage phase assignments in a separate SQLite table."""

    def __init__(self, database: Path) -> None:
        """Use the existing monitoring database without changing readings."""

        self.database = database

    def connect(self) -> sqlite3.Connection:
        """Open SQLite with a bounded busy timeout."""

        db = sqlite3.connect(self.database, timeout=10)
        db.execute("PRAGMA busy_timeout=5000")
        return db

    def initialize(self) -> None:
        """Create the table once; repeated calls preserve all assignments."""

        with closing(self.connect()) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS apartment_phases ("
                "building TEXT NOT NULL, apartment INTEGER NOT NULL, "
                "phase TEXT NOT NULL CHECK(phase IN ('L1','L2','L3')), "
                "PRIMARY KEY(building, apartment))"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_apartment_phases_phase "
                "ON apartment_phases(phase, building, apartment)"
            )
            existing = {
                row[1]
                for row in db.execute("PRAGMA table_info(apartment_phases)")
            }
            for name, constraint in (
                ("entrance", "BETWEEN 1 AND 100"),
                ("floor", "BETWEEN 0 AND 200"),
            ):
                if name not in existing:
                    db.execute(
                        f"ALTER TABLE apartment_phases ADD COLUMN {name} "
                        f"INTEGER CHECK({name} {constraint})"
                    )

    def assign(self, building: str, apartment: str, phase: str) -> None:
        """Add or move one apartment atomically."""

        building = valid_building(building)
        number = valid_apartment(apartment)
        phase = valid_phase(phase)
        with closing(self.connect()) as db, db:
            db.execute(
                "INSERT INTO apartment_phases(building, apartment, phase) "
                "VALUES(?,?,?) ON CONFLICT(building,apartment) "
                "DO UPDATE SET phase=excluded.phase",
                (building, number, phase),
            )

    def assign_range(
        self, building: str, start: str, end: str, phase: str
    ) -> int:
        """Assign one small apartment range in a single transaction."""

        building = valid_building(building)
        first = valid_apartment(start)
        last = valid_apartment(end)
        phase = valid_phase(phase)
        if last < first or last - first >= 200:
            raise ValueError("range must contain 1-200 apartments")
        entries = [
            (building, number, phase) for number in range(first, last + 1)
        ]
        with closing(self.connect()) as db, db:
            db.executemany(
                "INSERT INTO apartment_phases(building, apartment, phase) "
                "VALUES(?,?,?) ON CONFLICT(building,apartment) "
                "DO UPDATE SET phase=excluded.phase",
                entries,
            )
        return len(entries)

    def remove(self, building: str, apartment: str) -> bool:
        """Remove one assignment and report whether it existed."""

        building = valid_building(building)
        number = valid_apartment(apartment)
        with closing(self.connect()) as db, db:
            result = db.execute(
                "DELETE FROM apartment_phases "
                "WHERE building=? AND apartment=?",
                (building, number),
            )
            return result.rowcount > 0

    def set_location(
        self, building: str, apartment: str, entrance: str, floor: str
    ) -> None:
        """Record an entrance and floor for an assigned apartment."""

        building = valid_building(building)
        number = valid_apartment(apartment)
        entry = valid_location_number(entrance, "entrance", 1, 100)
        level = valid_location_number(floor, "floor", 0, 200)
        with closing(self.connect()) as db, db:
            result = db.execute(
                "UPDATE apartment_phases SET entrance=?, floor=? "
                "WHERE building=? AND apartment=?",
                (entry, level, building, number),
            )
            if result.rowcount == 0:
                raise ValueError("assign a phase to this apartment first")

    def list_locations(
        self,
        phase: str | None = None,
        building: str | None = None,
        entrance: int | None = None,
        floor: int | None = None,
    ) -> tuple[list[tuple], int]:
        """Find apartments using bound SQL parameters only."""

        filters = (
            ("phase", valid_phase(phase) if phase else None),
            ("building", valid_building(building) if building else None),
            ("entrance", entrance),
            ("floor", floor),
        )
        clauses = [f"{column}=?" for column, value in filters
                   if value is not None]
        params = tuple(value for _, value in filters if value is not None)
        predicate = "WHERE " + " AND ".join(clauses) if clauses else ""
        with closing(self.connect()) as db:
            total = db.execute(
                f"SELECT COUNT(*) FROM apartment_phases {predicate}", params
            ).fetchone()[0]
            rows = db.execute(
                "SELECT building,apartment,phase,entrance,floor "
                f"FROM apartment_phases {predicate} "
                "ORDER BY building,apartment LIMIT ?",
                (*params, MAX_LIST),
            ).fetchall()
        return rows, total

    def list_entries(
        self, phase: str | None = None
    ) -> tuple[list[tuple[str, int, str]], int]:
        """Return a bounded list and the total number of matching rows."""

        if phase is not None:
            phase = valid_phase(phase)
        predicate = "WHERE phase=?" if phase else ""
        params = (phase,) if phase else ()
        with closing(self.connect()) as db:
            total = db.execute(
                f"SELECT COUNT(*) FROM apartment_phases {predicate}", params
            ).fetchone()[0]
            rows = db.execute(
                "SELECT building,apartment,phase FROM apartment_phases "
                f"{predicate} ORDER BY building,apartment LIMIT ?",
                (*params, MAX_LIST),
            ).fetchall()
        return rows, total


def format_apartment_entry(
    building: str,
    apartment: int,
    phase: str,
    entrance: int | None,
    floor: int | None,
) -> str:
    """Render an apartment with optional building-location details."""

    label = f"{building} #{apartment}: {phase}"
    if entrance is not None and floor is not None:
        label += f" (entrance {entrance}, floor {floor})"
    return label


def format_apartment_list(
    registry: ApartmentRegistry,
    phase: str | None,
    building: str | None = None,
    entrance: int | None = None,
    floor: int | None = None,
) -> str:
    """Render a bounded, filtered apartment list."""

    rows, total = registry.list_locations(phase, building, entrance, floor)
    lines = [format_apartment_entry(*entry) for entry in rows]
    return (
        f"Apartments: {total}"
        + (" (first 100)" if total > MAX_LIST else "")
        + "\n"
        + ("\n".join(lines) if lines else "No assignments")
    )


def apartment_location_query(
    registry: ApartmentRegistry, parts: list[str]
) -> str:
    """List apartments by building, entrance or floor."""

    action = parts[1].lower()
    if action == "building" and len(parts) == 3:
        return format_apartment_list(registry, None, parts[2])
    if action == "entrance" and len(parts) == 4:
        entrance = valid_location_number(parts[3], "entrance", 1, 100)
        return format_apartment_list(registry, None, parts[2], entrance)
    if action == "floor" and len(parts) == 5:
        entrance = valid_location_number(parts[3], "entrance", 1, 100)
        level = valid_location_number(parts[4], "floor", 0, 200)
        return format_apartment_list(
            registry, None, parts[2], entrance, level
        )
    raise ValueError("invalid apartment location filter")


def apartment_command(
    registry: ApartmentRegistry, parts: list[str], allowed: set[str]
) -> str:
    """Execute one validated administrator command with plain responses."""

    usage = (
        "Usage: /apartments list [L1|L2|L3]; "
        "/apartments add BUILDING NUMBER PHASE; "
        "/apartments range BUILDING FIRST LAST PHASE; "
        "/apartments place BUILDING NUMBER ENTRANCE FLOOR; "
        "/apartments building BUILDING; "
        "/apartments entrance BUILDING NUMBER; "
        "/apartments floor BUILDING ENTRANCE FLOOR; "
        "/apartments remove BUILDING NUMBER"
    )
    if len(parts) < 2:
        return usage
    action = parts[1].lower()
    if action == "list" and len(parts) in (2, 3):
        phase = valid_phase(parts[2]) if len(parts) == 3 else None
        return format_apartment_list(registry, phase)
    if action in {"building", "entrance", "floor"}:
        return apartment_location_query(registry, parts)
    if action == "add" and len(parts) == 5:
        phase = valid_phase(parts[4])
        if phase not in allowed:
            raise ValueError("phase is not configured on this monitor")
        registry.assign(parts[2], parts[3], phase)
        return f"Saved: {parts[2]} #{int(parts[3])} → {phase}"
    if action == "range" and len(parts) == 6:
        phase = valid_phase(parts[5])
        if phase not in allowed:
            raise ValueError("phase is not configured on this monitor")
        count = registry.assign_range(parts[2], parts[3], parts[4], phase)
        return f"Saved {count} apartments in {parts[2]} on {phase}"
    if action == "place" and len(parts) == 6:
        registry.set_location(parts[2], parts[3], parts[4], parts[5])
        return f"Location saved: {parts[2]} #{int(parts[3])}"
    if action == "remove" and len(parts) == 4:
        removed = registry.remove(parts[2], parts[3])
        return "Removed" if removed else "Assignment not found"
    return usage
