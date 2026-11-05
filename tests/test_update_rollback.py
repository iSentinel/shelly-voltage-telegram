"""Exercise SQLite rollback without Docker or production resources."""

import gzip
import hashlib
import os
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

    def test_recovery_clears_public_version_without_deployment(self):
        """Restoring the original image clears stale version metadata."""

        state = self.root / "private"
        public = self.root / "public"
        state.mkdir()
        public.mkdir()
        (public / "deployed.json").write_text(
            '{"version":"0.5.13-hw"}', encoding="utf-8"
        )
        script = ROOT / "scripts" / "apply-update.sh"
        environment = {
            **os.environ,
            "SVT_UPDATE_STATE": str(state),
            "SVT_PUBLIC_STATE": str(public),
        }
        result = subprocess.run(
            [
                "bash",
                "-c",
                'source "$1"; sync_public_version',
                "bash",
                str(script),
            ],
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((public / "deployed.json").exists())

    def test_dry_run_cleans_workspace_and_exits_successfully(self):
        """A successful dry-run must not fail in its EXIT trap."""

        capture = self.root / "workspace_path"
        script = ROOT / "scripts" / "apply-update.sh"
        shell = "\n".join(
            (
                'source "$1"',
                'requirements() { :; }',
                'preflight() { :; }',
                'verify_image() { :; }',
                'verify_source_archive() { echo "$1" > "$CAPTURE"; }',
                'python3() { [[ "$2" != digest ]] || echo sha256:test; }',
                'main --version 0.5.12-hw --dry-run',
            )
        )
        env = {**os.environ, "CAPTURE": str(capture)}
        result = subprocess.run(
            ["bash", "-c", shell, "bash", str(script)],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DRY-RUN PASS", result.stdout)
        self.assertFalse(Path(capture.read_text().strip()).exists())

    def test_cosign_version_gate(self):
        """Require compatible Cosign 3 before changing host state."""

        script = ROOT / "scripts" / "apply-update.sh"
        for version, accepted in (
            ("v3.0.6", True),
            ("devel", False),
            ("v2.5.0", False),
        ):
            command = (
                'source "$1"; '
                'test_version="$2"; '
                'cosign() { printf "GitVersion: %s\\n" "$test_version"; }; '
                "check_cosign_compatibility"
            )
            result = subprocess.run(
                ["bash", "-c", command, "bash", str(script), version],
                capture_output=True,
                text=True,
                check=False,
            )
            with self.subTest(version=version):
                self.assertEqual(result.returncode == 0, accepted)

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

    def test_restore_does_not_overwrite_stale_temp_file(self):
        """A previous recovery temporary file must remain untouched."""

        stale = Path(str(self.database) + ".recovery.$")
        stale.write_text("previous interrupted attempt", encoding="utf-8")
        result = self.run_restore()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            stale.read_text(encoding="utf-8"),
            "previous interrupted attempt",
        )
        self.assertEqual(
            list(self.root.glob("monitor.sqlite3.recovery.????????")), []
        )
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(
                db.execute("SELECT id FROM samples").fetchone(), (42,)
            )

    def test_rescue_includes_uncheckpointed_wal_data(self):
        """Rescue must include committed rows still residing in the WAL."""

        writer = sqlite3.connect(self.database)
        try:
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("PRAGMA wal_autocheckpoint=0")
            writer.execute("UPDATE samples SET id=777")
            writer.commit()
            self.assertTrue(Path(str(self.database) + "-wal").exists())
            result = self.run_restore()
            self.assertEqual(result.returncode, 0, result.stderr)
            rescue = self.snapshot / "failed-upgrade-database.sqlite3"
            with closing(sqlite3.connect(rescue)) as db:
                self.assertEqual(
                    db.execute("SELECT id FROM samples").fetchone(), (777,)
                )
        finally:
            writer.close()

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
