"""Test interrupted updates on temporary state, without Docker access."""

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
OLD = {"version": "0.5.12-hw", "digest": "sha256:" + "a" * 64}
NEW = {"version": "0.5.13-hw", "digest": "sha256:" + "b" * 64}


def override(receipt):
    """Build the same immutable pin as the legacy updater."""
    image = "ghcr.io/isentinel/shelly-voltage-telegram"
    return (
        "services:\n  shelly-voltage:\n"
        f"    image: {image}@{receipt['digest']}\n"
    )


class InterruptedUpdateTests(unittest.TestCase):
    """Verify durable recovery of an interrupted image replacement."""

    def setUp(self):
        """Create a private update journal, old backup and newer state."""

        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.state = self.directory / "state"
        self.snapshot = self.state / "snapshots" / SNAPSHOT_NAME
        for directory in (self.state, self.snapshot.parent, self.snapshot):
            directory.mkdir(mode=0o700)
        self.database = self.directory / "monitor.sqlite3"
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("CREATE TABLE measurements (id INTEGER, voltage REAL)")
            db.execute("INSERT INTO measurements VALUES (42, 220.0)")
        self.archive = self.snapshot / "voltage-test.sqlite3.gz"
        self.archive.write_bytes(gzip.compress(self.database.read_bytes()))
        checksum = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.manifest = self.archive.with_suffix(".gz.sha256")
        self.manifest.write_text(
            f"{checksum}  {self.archive}\n", encoding="utf-8"
        )
        for folder, receipt in ((self.state, NEW), (self.snapshot, OLD)):
            (folder / "override.yaml").write_text(override(receipt))
            (folder / "deployed.json").write_text(json.dumps(receipt))
            for name in ("override.yaml", "deployed.json"):
                (folder / name).chmod(0o600)
        (self.state / "pending-update").write_text(SNAPSHOT_NAME + "\n")
        for path in (*self.snapshot.iterdir(), self.state / "pending-update"):
            path.chmod(0o600)
        self.public = self.directory / "public"
        self.stops = self.directory / "stops"

    def recover(self, stop_command="", running_writer=""):
        """Run shell recovery with a disposable stop callback."""

        command = (
            'source "$1"; STATE="$2"; DB="$3"; ROOT="$4"; '
            'PUBLIC_STATE="$5"; PENDING_FILE="$STATE/pending-update"; '
            'STOP_LOG="$6"; STOP_COMMAND="$7"; '
            'RUNNING_WRITER="$8"; '
            'docker() { if [[ "$1" == ps ]]; then '
            'printf "%s" "$RUNNING_WRITER"; return; fi; '
            'echo stop >> "$STOP_LOG"; '
            'if [[ -n "$STOP_COMMAND" ]]; then '
            'python3 -c "$STOP_COMMAND" "$DB"; fi; }; '
            "recover_pending"
        )
        return subprocess.run(
            [
                "bash",
                "-c",
                command,
                "bash",
                str(ROOT / "scripts" / "apply-update.sh"),
                str(self.state),
                str(self.database),
                str(ROOT),
                str(self.public),
                str(self.stops),
                stop_command,
                running_writer,
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    def sample(self):
        """Read the isolated active database."""

        with closing(sqlite3.connect(self.database)) as db:
            return db.execute("SELECT id FROM measurements").fetchone()[0]

    def assert_recovery_pending(self):
        """Check that the selected image and recovery intent are intact."""

        self.assertTrue((self.state / "pending-update").exists())
        self.assertEqual(
            (self.state / "override.yaml").read_text(), override(NEW)
        )

    def test_pending_marker_is_written_before_image_selection(self):
        """Journal a unique snapshot before replacing the Compose image."""

        (self.state / "pending-update").unlink()
        command = (
            'source "$1"; STATE="$2"; '
            'PENDING_FILE="$STATE/pending-update"; '
            'SNAPSHOT="$STATE/snapshots/$3"; mark_pending'
        )
        result = subprocess.run(
            [
                "bash",
                "-c",
                command,
                "bash",
                str(ROOT / "scripts" / "apply-update.sh"),
                str(self.state),
                SNAPSHOT_NAME,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (self.state / "pending-update").read_text().strip(),
            SNAPSHOT_NAME,
        )

    def test_matching_archive_restores_image_and_keeps_database_file(self):
        """Roll back only the image selection after exact history parity."""

        before = self.database.stat().st_ino, self.database.read_bytes()
        result = self.recover()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.sample(), 42)
        self.assertEqual(
            (self.state / "override.yaml").read_text(), override(OLD)
        )
        self.assertEqual(
            json.loads((self.state / "deployed.json").read_text())["version"],
            OLD["version"],
        )
        self.assertFalse((self.state / "pending-update").exists())
        self.assertEqual(
            json.loads((self.public / "deployed.json").read_text())["version"],
            OLD["version"],
        )
        self.assertEqual(
            (self.database.stat().st_ino, self.database.read_bytes()), before
        )
        self.assertEqual(self.stops.read_text().splitlines(), ["stop"])

    def test_new_committed_wal_samples_block_recovery_before_stop(self):
        """SQLite reads include current committed rows still in the WAL."""

        with closing(sqlite3.connect(self.database)) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA wal_autocheckpoint=0")
            db.execute("INSERT INTO measurements VALUES (99, 223.0)")
            db.commit()
            wal = self.database.with_suffix(".sqlite3-wal")
            before = wal.read_bytes()
            result = self.recover()
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("does not match live SQLite", result.stderr)
            self.assertEqual(
                db.execute(
                    "SELECT id FROM measurements ORDER BY id"
                ).fetchall(),
                [(42,), (99,)],
            )
            self.assertEqual(wal.read_bytes(), before)
        self.assertFalse(self.stops.exists())
        self.assert_recovery_pending()

    def test_changed_reading_with_same_count_blocks_before_stop(self):
        """Equal row counts are insufficient for a recovery decision."""

        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE measurements SET voltage=171.0")
        result = self.recover()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.stops.exists())
        self.assert_recovery_pending()

    def test_last_write_during_stop_blocks_image_change(self):
        """Check parity again after Docker had a chance to finish a write."""

        command = (
            "import sqlite3, sys; db = sqlite3.connect(sys.argv[1]); "
            "db.execute('UPDATE measurements SET id=99'); db.commit(); "
            "db.close()"
        )
        result = self.recover(command)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.stops.read_text().splitlines(), ["stop"])
        self.assertEqual(self.sample(), 99)
        self.assert_recovery_pending()

    def test_stop_returning_success_is_insufficient_with_live_writer(self):
        """A still-running Docker writer keeps the old recovery intent."""
        result = self.recover(running_writer="some-container-id")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("writer is still running", result.stderr)
        self.assertEqual(self.stops.read_text().splitlines(), ["stop"])
        self.assertEqual(self.sample(), 42)
        self.assert_recovery_pending()

    def test_sqlite_user_version_change_blocks_recovery(self):
        """Schema version metadata participates in history comparison."""

        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("PRAGMA user_version=2")
        result = self.recover()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.stops.exists())
        self.assert_recovery_pending()

    def test_invalid_checksum_keeps_pending_marker(self):
        """Block startup and preserve the journal when restoration fails."""

        self.manifest.write_text("invalid checksum\n")
        result = self.recover()
        self.assertNotEqual(result.returncode, 0)
        self.assert_recovery_pending()
        self.assertEqual(self.sample(), 42)
        self.assertFalse(self.stops.exists())

    def test_checksum_bound_to_different_archive_is_rejected(self):
        """A valid checksum for another file must not satisfy the guard."""

        another = self.directory / "another.sqlite3.gz"
        another.write_bytes(self.archive.read_bytes())
        digest = hashlib.sha256(another.read_bytes()).hexdigest()
        self.manifest.write_text(f"{digest}  {another}\n")
        result = self.recover()
        self.assertNotEqual(result.returncode, 0)
        self.assert_recovery_pending()
        self.assertFalse(self.stops.exists())

    def test_archive_symlink_is_rejected(self):
        """A linked archive cannot be used for recovery."""

        saved = self.directory / "saved.sqlite3.gz"
        self.archive.rename(saved)
        self.archive.symlink_to(saved)
        result = self.recover()
        self.assertNotEqual(result.returncode, 0)
        self.assert_recovery_pending()
        self.assertFalse(self.stops.exists())

    def test_no_pending_marker_leaves_state_unchanged(self):
        """A normal startup does not touch the database or image."""

        (self.state / "pending-update").unlink()
        result = self.recover()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.sample(), 42)
        self.assertEqual(
            (self.state / "override.yaml").read_text(), override(NEW)
        )
        self.assertEqual(
            json.loads((self.public / "deployed.json").read_text()),
            NEW,
        )

    def test_failed_public_sync_does_not_repeat_pending_recovery(self):
        """A publication failure keeps completed recovery committed."""

        self.public.write_text("occupied public path")
        first = self.recover()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("retry --sync-public-version", first.stderr)
        self.assertFalse((self.state / "pending-update").exists())
        self.assertEqual(self.sample(), 42)
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE measurements SET id=123")
        self.public.unlink()
        second = self.recover()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.sample(), 123)
        self.assertEqual(self.stops.read_text().splitlines(), ["stop"])
        self.assertEqual(
            json.loads((self.public / "deployed.json").read_text()),
            OLD,
        )

    def test_untrusted_snapshot_name_is_rejected(self):
        """Reject paths escaping the protected snapshot directory."""

        (self.state / "pending-update").write_text("../escape\n")
        result = self.recover()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.sample(), 42)
        self.assertTrue((self.state / "pending-update").exists())


if __name__ == "__main__":
    unittest.main()
