"""Tests for the administrator-managed apartment phase registry."""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from apartment_phases import ApartmentRegistry, apartment_command


class ApartmentRegistryTests(unittest.TestCase):
    """Cover assignments, validation, privacy and restart persistence."""

    def setUp(self):
        """Create an isolated temporary database."""

        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "monitor.sqlite3"
        self.registry = ApartmentRegistry(self.path)
        self.registry.initialize()

    def test_add_move_remove_and_restart(self):
        """Move an apartment without duplication and persist its phase."""

        self.registry.assign("A", "12", "L1")
        self.registry.assign("A", "12", "L2")
        restarted = ApartmentRegistry(self.path)
        restarted.initialize()
        rows, count = restarted.list_entries()
        self.assertEqual(count, 1)
        self.assertEqual(rows, [("A", 12, "L2")])
        self.assertTrue(restarted.remove("A", "12"))
        self.assertFalse(restarted.remove("A", "12"))
        self.assertEqual(restarted.list_entries(), ([], 0))

    def test_phase_filter_and_separate_buildings(self):
        """Apartments with the same number may belong to other buildings."""

        self.registry.assign("A", "5", "L1")
        self.registry.assign("B", "5", "L2")
        self.assertEqual(
            self.registry.list_entries("L1"), ([("A", 5, "L1")], 1)
        )
        self.assertEqual(
            self.registry.list_entries("L2"), ([("B", 5, "L2")], 1)
        )

    def test_bad_input_does_not_change_rows(self):
        """Reject invalid names, numbers and phase labels."""

        for building, apartment, phase in (
            ("A B", "3", "L1"),
            ("A", "0", "L1"),
            ("A", "-1", "L1"),
            ("A", "1", "L4"),
            ("../../etc", "1", "L1"),
        ):
            with (
                self.subTest(building=building, apartment=apartment),
                self.assertRaises(ValueError),
            ):
                self.registry.assign(building, apartment, phase)
        self.assertEqual(self.registry.list_entries(), ([], 0))

    def test_repeated_initialization_preserves_existing_tables(self):
        """Schema initialization must not clear monitoring history."""

        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TABLE measurements (id INTEGER PRIMARY KEY)")
            db.execute("INSERT INTO measurements VALUES (1)")
        self.registry.assign("A", "1", "L1")
        self.registry.initialize()
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM measurements").fetchone()[0],
                1,
            )
        self.assertEqual(self.registry.list_entries()[1], 1)

    def test_telegram_command_and_active_phase_guard(self):
        """Prevent assignment to a phase without a configured meter."""

        response = apartment_command(
            self.registry, ["/apartments", "add", "A", "3", "L"], {"L1"}
        )
        self.assertIn("Saved", response)
        self.assertIn(
            "A #3: L1",
            apartment_command(self.registry, ["/apartments", "list"], {"L1"}),
        )
        with self.assertRaises(ValueError):
            apartment_command(
                self.registry, ["/apartments", "add", "A", "4", "L2"], {"L1"}
            )
        self.assertEqual(self.registry.list_entries()[1], 1)

    def test_atomic_range_and_invalid_bounds(self):
        """One range can add or move apartments without partial writes."""

        result = apartment_command(
            self.registry,
            ["/apartments", "range", "A", "1", "30", "L1"],
            {"L1"},
        )
        self.assertIn("Saved 30", result)
        self.assertEqual(self.registry.list_entries()[1], 30)
        self.registry.assign_range("A", "10", "15", "L2")
        self.assertEqual(self.registry.list_entries("L2")[1], 6)
        for first, last in (("50", "40"), ("1", "201"), ("0", "3")):
            with (
                self.subTest(first=first, last=last),
                self.assertRaises(ValueError),
            ):
                self.registry.assign_range("A", first, last, "L1")
        self.assertEqual(self.registry.list_entries()[1], 30)

    def test_listing_is_bounded(self):
        """Return a bounded listing while preserving an accurate total."""

        for number in range(1, 104):
            self.registry.assign("A", str(number), "L1")
        rows, total = self.registry.list_entries()
        self.assertEqual(len(rows), 100)
        self.assertEqual(total, 103)


if __name__ == "__main__":
    unittest.main()
