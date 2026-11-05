"""Exercise interrupted updates without touching production Docker or SQLite."""

import gzip
import hashlib
import json
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_NAME = "20261105T120000Z-0.5.13-hw"


class InterruptedUpdateTests(unittest.TestCase):
    """Verify durable recovery of an interrupted image replacement."""

    def setUp(self):
        """Create a private update journal, old backup and newer state."""

        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.state = self.directory / "state"
        self.snapshot = self.state / "snapshots" / SNAPSHOT_NAME
        self.snapshot.mkdir(parents=True)
        self.project = self.directory / "project"
        self.project.mkdir()
        (self.project / "compose.yaml").write_text("services: {}\n")
        self.database = self.directory / "monitor.sqlite3"
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("CREATE TABLE samples (id INTEGER)")
            db.execute("INSERT INTO samples VALUES (42)")
        backup = self.snapshot / "voltage-test.sqlite3.gz"
        backup.write_bytes(gzip.compress(self.database.read_bytes()))
        checksum = hashlib.sha256(backup.read_bytes()).hexdigest()
        Path(str(backup) + ".sha256").write_text(
            f"{checksum}  {backup}\n", encoding="utf-8"
        )
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE samples SET id=99")
        (self.state / "override.yaml").write_text("image: new\n")
        (self.snapshot / "override.yaml").write_text("image: old\n")
        (self.state / "deployed.json").write_text('{"version":"new"}')
        (self.snapshot / "deployed.json").write_text('{"version":"old"}')
        (self.state / "pending-update").write_text(SNAPSHOT_NAME + "\n")
        self.public = self.directory / "public"

    def recover(self):
        """Run only shell recovery with Docker and ownership calls mocked."""

        command = (
            'source "$1"; STATE="$2"; DB="$3"; ROOT="$4"; '
            'PUBLIC_STATE="$5"; PENDING_FILE="$STATE/pending-update"; '
            "docker() { return 0; }; chown() { return 0; }; "
            "recover_pending"
        )
        return subprocess.run(
            [
                "bash", "-c", command, "bash",
                str(ROOT / "scripts" / "apply-update.sh"),
                str(self.state), str(self.database), str(self.project),
                str(self.public),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    def sample(self):
        """Read the isolated active database."""

        with closing(sqlite3.connect(self.database)) as db:
            return db.execute("SELECT id FROM samples").fetchone()[0]

    def test_interrupted_candidate_restores_old_image_and_data(self):
        """Roll back and preserve a rescue copy of the newer data."""

        result = self.recover()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.sample(), 42)
        self.assertEqual(
            (self.state / "override.yaml").read_text(), "image: old\n"
        )
        self.assertEqual(
            json.loads((self.state / "deployed.json").read_text())["version"],
            "old",
        )
        self.assertFalse((self.state / "pending-update").exists())
        self.assertEqual(
            json.loads((self.public / "deployed.json").read_text())["version"],
            "old",
        )
        rescue = self.snapshot / "failed-upgrade-database.sqlite3"
        with closing(sqlite3.connect(rescue)) as db:
            self.assertEqual(
                db.execute("SELECT id FROM samples").fetchone()[0], 99
            )

    def test_invalid_checksum_keeps_pending_marker(self):
        """Block startup and preserve the journal when restoration fails."""

        checksum = self.snapshot / "voltage-test.sqlite3.gz.sha256"
        checksum.write_text("invalid checksum\n")
        result = self.recover()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.state / "pending-update").exists())
        self.assertEqual(self.sample(), 99)

    def test_no_pending_marker_leaves_state_unchanged(self):
        """A normal startup does not touch the database or image."""

        (self.state / "pending-update").unlink()
        result = self.recover()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.sample(), 99)
        self.assertEqual(
            (self.state / "override.yaml").read_text(), "image: new\n"
        )

    def test_untrusted_snapshot_name_is_rejected(self):
        """Reject paths escaping the protected snapshot directory."""

        (self.state / "pending-update").write_text("../escape\n")
        result = self.recover()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.sample(), 99)
        self.assertTrue((self.state / "pending-update").exists())


if __name__ == "__main__":
    unittest.main()
