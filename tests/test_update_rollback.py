"""Exercise SQLite rollback without Docker or production resources."""

import gzip
import hashlib
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class OfflineRollbackTests(unittest.TestCase):
    """Keep real monitoring data out of rollback unit tests."""

    def setUp(self):
        """Create a disposable database and backup directory."""

        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.database = self.root / "monitor.sqlite3"
        self.snapshot = self.root / "snapshot"
        self.snapshot.mkdir()
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("CREATE TABLE samples (id INTEGER)")
            db.execute("INSERT INTO samples VALUES (42)")
        raw = self.database.read_bytes()
        self.backup = self.snapshot / "voltage-test.sqlite3.gz"
        self.backup.write_bytes(gzip.compress(raw))
        digest = hashlib.sha256(self.backup.read_bytes()).hexdigest()
        (self.snapshot / (self.backup.name + ".sha256")).write_text(
            f"{digest}  {self.backup}\n", encoding="utf-8"
        )
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE samples SET id=99")

    def run_restore(self):
        """Run only rollback functions with a mocked Compose command."""

        script = ROOT / "scripts" / "apply-update.sh"
        shell = (
            'source "$1"; '
            'SNAPSHOT="$2"; DB="$3"; ROOT="$4"; '
            "docker() { return 0; }; "
            "chown() { return 0; }; "
            "restore_snapshot_database"
        )
        return subprocess.run(
            [
                "bash",
                "-c",
                shell,
                "bash",
                str(script),
                str(self.snapshot),
                str(self.database),
                str(self.root),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_valid_snapshot_restores_previous_database(self):
        """Recover old records and retain post-failure data separately."""

        result = self.run_restore()
        self.assertEqual(result.returncode, 0, result.stderr)
        with closing(sqlite3.connect(self.database)) as db, db:
            self.assertEqual(
                db.execute("SELECT id FROM samples").fetchone(), (42,)
            )
        rescue = self.snapshot / "failed-upgrade-database.sqlite3"
        with closing(sqlite3.connect(rescue)) as db:
            self.assertEqual(
                db.execute("SELECT id FROM samples").fetchone(), (99,)
            )

    def test_bad_checksum_leaves_active_database_unchanged(self):
        """Reject a corrupted backup before any database replacement."""

        (self.snapshot / (self.backup.name + ".sha256")).write_text(
            "0" * 64 + f"  {self.backup}\n",
            encoding="utf-8",
        )
        result = self.run_restore()
        self.assertNotEqual(result.returncode, 0)
        with closing(sqlite3.connect(self.database)) as db, db:
            self.assertEqual(
                db.execute("SELECT id FROM samples").fetchone(), (99,)
            )
        self.assertFalse(
            (self.snapshot / "failed-upgrade-database.sqlite3").exists()
        )


if __name__ == "__main__":
    unittest.main()
