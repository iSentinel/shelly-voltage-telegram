"""Validate archived SQLite content before rollback can stop Docker."""

from __future__ import annotations

import gzip
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/validate-sqlite-archive.py"


class SQLiteArchiveTests(unittest.TestCase):
    """Exercise a disposable database and its gzip archive."""

    def setUp(self):
        """Create a valid archive without using production files."""

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        dbpath = self.root / "sample.sqlite3"
        with sqlite3.connect(dbpath) as db:
            db.execute("CREATE TABLE measurements(voltage REAL)")
            db.execute("INSERT INTO measurements VALUES (230.0)")
        self.archive = self.root / "voltage-test.sqlite3.gz"
        self.archive.write_bytes(gzip.compress(dbpath.read_bytes()))

    def run_check(self, *args: str) -> subprocess.CompletedProcess:
        """Invoke the checker without loading application dependencies."""

        return subprocess.run(
            ["python3", str(SCRIPT), "--archive", str(self.archive), *args],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_valid_backup_passes(self):
        """A readable database passes integrity and table checks."""

        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("1 restored measurements", result.stdout)

    def test_broken_gzip_fails(self):
        """Reject an invalid compressed stream."""

        self.archive.write_bytes(b"not gzip")
        self.assertEqual(self.run_check().returncode, 2)

    def test_corrupt_sqlite_fails(self):
        """A well-formed gzip is insufficient without a valid database."""

        self.archive.write_bytes(gzip.compress(b"not sqlite"))
        self.assertEqual(self.run_check().returncode, 2)

    def test_size_limit_fails(self):
        """Stop extraction when the uncompressed SQLite exceeds the limit."""

        oversized = self.root / "large.sqlite3"
        with sqlite3.connect(oversized) as db:
            db.execute("CREATE TABLE measurements(voltage REAL)")
            db.execute("CREATE TABLE padding(blob BLOB)")
            db.execute("INSERT INTO padding VALUES (?)", (bytes(2_000_000),))
        self.archive.write_bytes(gzip.compress(oversized.read_bytes()))
        self.assertEqual(self.run_check("--max-size-mib", "1").returncode, 2)

    def test_missing_and_symlink_archives_fail(self):
        """Do not use missing or symbolic-link archives."""

        missing = self.root / "missing.sqlite3.gz"
        self.assertEqual(self.run_check("--max-size-mib", "0").returncode, 2)
        missing.symlink_to(self.archive)
        result = subprocess.run(
            ["python3", str(SCRIPT), "--archive", str(missing)],
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)

    def test_help_is_present(self):
        """Accept -h and describe the required archive flag."""

        result = subprocess.run(
            ["python3", str(SCRIPT), "--help"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("--archive", result.stdout)


if __name__ == "__main__":
    unittest.main()
