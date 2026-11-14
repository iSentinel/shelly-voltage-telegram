"""Test interrupted updates on temporary state, without Docker access."""

import gzip
import hashlib
import json
import shutil
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
        (self.project / "scripts").mkdir()
        shutil.copy2(
            ROOT / "scripts/pending-recovery-history-guard.py",
            self.project / "scripts/pending-recovery-history-guard.py",
        )
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

    def recover(self, injected_shell=""):
        """Run only isolated recovery with mocked Docker commands."""

        command = (
            'source "$1"; STATE="$2"; DB="$3"; ROOT="$4"; '
            'PUBLIC_STATE="$5"; PENDING_FILE="$STATE/pending-update"; '
            "docker() { return 0; }; chown() { return 0; }; "
            + (injected_shell + "; " if injected_shell else "")
            + "recover_pending"
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
                str(self.project),
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

    def test_interrupted_candidate_blocks_stale_database(self):
        """Newer samples prevent both archive rewind and metadata rewind."""

        result = self.recover()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.sample(), 99)
        self.assertEqual(
            (self.state / "override.yaml").read_text(), "image: new\n"
        )
        self.assertEqual(
            json.loads((self.state / "deployed.json").read_text())["version"],
            "new",
        )
        self.assertTrue((self.state / "pending-update").exists())
        self.assertFalse((self.public / "deployed.json").exists())
        self.assertFalse(
            (self.snapshot / "failed-upgrade-database.sqlite3").exists()
        )

    def test_stale_archive_does_not_stop_online_writer(self):
        """Reject stale pending recovery before any Docker stop command."""

        marker = self.directory / "unexpected-docker-stop"
        injected = 'docker() { printf "stop" > "$6"; return 0; }'
        command = (
            'source "$1"; STATE="$2"; DB="$3"; ROOT="$4"; '
            'PUBLIC_STATE="$5"; PENDING_FILE="$STATE/pending-update"; '
            + injected
            + "; recover_pending"
        )
        result = subprocess.run(
            [
                "bash",
                "-c",
                command,
                "bash",
                str(ROOT / "scripts/apply-update.sh"),
                str(self.state),
                str(self.database),
                str(self.project),
                str(self.public),
                str(marker),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(marker.exists())
        self.assertEqual(self.sample(), 99)
        self.assertTrue((self.state / "pending-update").exists())
        self.assertEqual(
            (self.state / "override.yaml").read_text(), "image: new\n"
        )

    def test_new_wal_row_during_compose_stop_fails_final_guard(self):
        """A fresh commit during stop must not be replaced or ignored."""

        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE samples SET id=42")
        injected = (
            'docker() { python3 -c "import sqlite3,sys; '
            "db=sqlite3.connect(sys.argv[1]); "
            "db.execute('UPDATE samples SET id=777'); "
            'db.commit(); db.close()" "$DB"; return 0; }'
        )
        failed = self.recover(injected)
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(self.sample(), 777)
        self.assertTrue((self.state / "pending-update").exists())
        self.assertEqual(
            (self.state / "override.yaml").read_text(), "image: new\n"
        )

    def test_invalid_backup_checksum_does_not_stop_writer(self):
        """A tampered archive checksum is rejected before Docker stop."""

        checksum = self.snapshot / "voltage-test.sqlite3.gz.sha256"
        checksum.write_text("0" * 64 + "  invalid.gz\\n")
        self.assert_not_stopped_when_recovery_blocked()

    def test_missing_live_database_does_not_stop_writer(self):
        """A missing established SQLite is checked before Docker stop."""

        self.database.unlink()
        self.assert_not_stopped_when_recovery_blocked()

    def test_equal_history_still_stops_test_writer_once(self):
        """Exact parity may proceed to one controlled Compose stop."""

        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE samples SET id=42")
        result = self.recover(
            'docker() { echo stopped >> "$STATE/docker.called"; return 0; }'
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = (self.state / "docker.called").read_text().splitlines()
        self.assertEqual(calls, ["stopped"])
        self.assertEqual(self.sample(), 42)

    def assert_not_stopped_when_recovery_blocked(self):
        """Shared stub ensures history failures never reach Docker stop."""

        result = self.recover(
            'docker() { echo stopped >> "$STATE/docker.called"; return 0; }'
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.state / "docker.called").exists())
        self.assertTrue((self.state / "pending-update").exists())

    def test_exact_match_rolls_back_image_but_preserves_database(self):
        """Identical archive and current history need no SQLite replacement."""

        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE samples SET id=42")
        original_inode = self.database.stat().st_ino
        result = self.recover()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.sample(), 42)
        self.assertEqual(self.database.stat().st_ino, original_inode)
        self.assertEqual(
            (self.state / "override.yaml").read_text(), "image: old\n"
        )
        self.assertFalse((self.state / "pending-update").exists())
        self.assertEqual(
            json.loads((self.public / "deployed.json").read_text())["version"],
            "old",
        )

    def test_retry_keeps_original_post_failure_rescue(self):
        """Retrying recovery must not replace the first rescue snapshot."""

        rescue = self.snapshot / "failed-upgrade-database.sqlite3"
        with closing(sqlite3.connect(rescue)) as db, db:
            db.execute("CREATE TABLE samples (id INTEGER)")
            db.execute("INSERT INTO samples VALUES (777)")
        result = self.recover()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.sample(), 99)
        self.assertTrue((self.state / "pending-update").exists())
        with closing(sqlite3.connect(rescue)) as db:
            self.assertEqual(
                db.execute("SELECT id FROM samples").fetchone()[0], 777
            )

    def test_config_copy_failure_keeps_pending(self):
        """A failed config copy must not falsely complete recovery."""

        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE samples SET id=42")
        result = self.recover("cp() { return 18; }")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.sample(), 42)
        self.assertTrue((self.state / "pending-update").exists())
        self.assertEqual(
            (self.state / "override.yaml").read_text(), "image: new\n"
        )

    def test_version_publication_failure_keeps_pending(self):
        """Failure after config restore must still retain the journal."""

        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE samples SET id=42")
        result = self.recover("sync_public_version() { return 19; }")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.sample(), 42)
        self.assertTrue((self.state / "pending-update").exists())

    def test_retry_after_publication_failure_succeeds(self):
        """A retry on exact history can complete a pending recovery."""

        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE samples SET id=42")
        first = self.recover("sync_public_version() { return 20; }")
        self.assertNotEqual(first.returncode, 0)
        second = self.recover()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertFalse((self.state / "pending-update").exists())
        self.assertEqual(self.sample(), 42)

    def test_writer_race_after_metadata_copy_keeps_pending(self):
        """A new DB write between two guard checks must block completion."""

        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE samples SET id=42")
        injected = (
            'cp() { command cp "$@" || return; '
            'python3 -c "import sqlite3,sys; '
            "db=sqlite3.connect(sys.argv[1]); "
            "db.execute('UPDATE samples SET id=777'); "
            'db.commit(); db.close()" "$DB"; }'
        )
        result = self.recover(injected)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.sample(), 777)
        self.assertTrue((self.state / "pending-update").exists())

    def test_sigkill_during_metadata_restore_retains_latest_history(self):
        """A crash may not clear pending or rewind committed telemetry."""

        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE samples SET id=42")
        injected = 'cp() { command cp "$@" || return; kill -KILL "$BASHPID"; }'
        failed = self.recover(injected)
        self.assertNotEqual(failed.returncode, 0)
        self.assertTrue((self.state / "pending-update").exists())
        self.assertEqual(self.sample(), 42)
        retried = self.recover()
        self.assertEqual(retried.returncode, 0, retried.stderr)
        self.assertFalse((self.state / "pending-update").exists())
        self.assertEqual(self.sample(), 42)

    def test_missing_live_database_does_not_restore_archive(self):
        """A missing established SQLite must require owner review."""

        self.database.unlink()
        failed = self.recover()
        self.assertNotEqual(failed.returncode, 0)
        self.assertFalse(self.database.exists())
        self.assertTrue((self.state / "pending-update").exists())
        self.assertEqual(
            (self.state / "override.yaml").read_text(), "image: new\n"
        )

    def test_rollback_error_does_not_restart_stack_on_stale_data(self):
        """A failed history guard may never start the old Compose image."""

        attempted = self.directory / "unexpected-restart.marker"
        command = (
            'source "$1"; STATE="$2"; DB="$3"; ROOT="$4"; '
            'PUBLIC_STATE="$5"; PENDING_FILE="$STATE/pending-update"; '
            "CHANGED=true; "
            "docker() { return 0; }; "
            'start_stack() { echo unexpected > "$6"; }; '
            "rollback"
        )
        result = subprocess.run(
            [
                "bash",
                "-c",
                command,
                "bash",
                str(ROOT / "scripts/apply-update.sh"),
                str(self.state),
                str(self.database),
                str(self.project),
                str(self.public),
                str(attempted),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(attempted.exists())
        self.assertTrue((self.state / "pending-update").exists())
        self.assertEqual(self.sample(), 99)
        self.assertEqual(
            (self.state / "override.yaml").read_text(), "image: new\n"
        )

    def test_pending_marker_removal_failure_remains_pending(self):
        """A failed final journal deletion must not report recovery done."""

        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE samples SET id=42")
        result = self.recover("rm() { return 21; }")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.sample(), 42)
        self.assertTrue((self.state / "pending-update").exists())

    def test_snapshot_mutation_during_metadata_copy_is_blocked(self):
        """A changed archived file cannot survive the final digest check."""

        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE samples SET id=42")
        injected = (
            'cp() { command cp "$@" || return; '
            'printf corrupted > "$SNAPSHOT/voltage-test.sqlite3.gz"; }'
        )
        result = self.recover(injected)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.state / "pending-update").exists())
        self.assertEqual(self.sample(), 42)

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
