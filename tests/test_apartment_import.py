"""Test bounded apartment CSV validation and atomic imports."""

from __future__ import annotations

import csv
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from apartment_phases import ApartmentRegistry
from scripts.import_apartments import main, read_rows

FIELDS = ("building", "apartment", "phase", "entrance", "floor")


class ApartmentCsvTests(unittest.TestCase):
    """Validate dry-run defaults, persistence and rejection of bad input."""

    def setUp(self):
        """Create an isolated CSV file and SQLite database."""

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.csv_file = self.root / "apartments.csv"
        self.db_path = self.root / "voltage.sqlite3"
        self.registry = ApartmentRegistry(self.db_path)
        self.registry.initialize()

    def write_csv(self, rows):
        """Write an explicit five-column CSV fixture."""

        with self.csv_file.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(FIELDS)
            writer.writerows(rows)

    def execute(self, *arguments):
        """Run the CLI quietly and return its exit code and output."""

        output = StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            result = main(
                [
                    "--csv",
                    str(self.csv_file),
                    "--db",
                    str(self.db_path),
                    *arguments,
                ]
            )
        return result, output.getvalue()

    def test_dry_run_does_not_create_assignments(self):
        """Validate imported data without writing to the database."""

        self.write_csv([("A", "12", "L1", "2", "5")])
        code, message = self.execute()
        self.assertEqual(code, 0, message)
        self.assertIn("dry-run", message)
        self.assertEqual(self.registry.list_entries(), ([], 0))

    def test_apply_is_atomic_and_idempotent(self):
        """Apply the entire file, then repeat it without duplicates."""

        self.write_csv(
            [
                ("A", "12", "L1", "2", "5"),
                ("A", "13", "L1", "", ""),
            ]
        )
        for _ in range(2):
            code, message = self.execute("--apply")
            self.assertEqual(code, 0, message)
        self.assertEqual(self.registry.list_entries()[1], 2)
        self.assertEqual(self.registry.list_locations()[0][0][3:], (2, 5))

    def test_invalid_row_cannot_partially_modify_database(self):
        """Reject a whole CSV containing one invalid phase."""

        self.registry.assign("A", "8", "L1")
        self.write_csv(
            [
                ("A", "12", "L1", "", ""),
                ("A", "13", "L3", "", ""),
            ]
        )
        code, message = self.execute("--apply")
        self.assertEqual(code, 1)
        self.assertIn("line 3", message)
        self.assertEqual(self.registry.list_entries(), ([("A", 8, "L1")], 1))

    def test_reject_duplicates_and_excess_rows(self):
        """Protect against ambiguous and unbounded imports."""

        self.write_csv(
            [
                ("A", "1", "L1", "", ""),
                ("A", "1", "L2", "", ""),
            ]
        )
        with self.assertRaisesRegex(ValueError, "duplicate"):
            read_rows(self.csv_file, {"L1", "L2"})
        self.write_csv([("A", str(i), "L1", "", "") for i in range(1, 1002)])
        with self.assertRaisesRegex(ValueError, "1000-row"):
            read_rows(self.csv_file, {"L1"})

    def test_blank_locations_preserve_existing_metadata(self):
        """An import without locations does not erase saved locations."""

        self.registry.assign("A", "12", "L1")
        self.registry.set_location("A", "12", "2", "5")
        self.write_csv([("A", "12", "L1", "", "")])
        code, message = self.execute("--apply")
        self.assertEqual(code, 0, message)
        self.assertEqual(self.registry.list_locations()[0][0][3:], (2, 5))


if __name__ == "__main__":
    unittest.main()
