"""Guarded source and database migration readiness without Telegram calls."""

from __future__ import annotations

import gzip
import hashlib
import io
import os
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / (
    "scripts/graphic-rollout-readiness.py"
)
FILES = (
    "VERSION",
    "app.py",
    "Dockerfile",
    "compose.yaml",
    "requirements.txt",
    "scripts/backup.sh",
    "systemd/shelly-voltage-compose.service",
    "systemd/shelly-voltage-backup.service",
)


def seal_file(path: Path) -> None:
    """Store a matching hash bound to its absolute fixture path."""

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    Path(str(path) + ".sha256").write_text(f"{digest}  {path}\n")


class GraphicRolloutReadinessTests(unittest.TestCase):
    """Use disposable independent snapshots to prove the rollout gate."""

    def setUp(self):
        """Prepare synthetic sqlite files and valid source rollback tarball."""

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.database = root / "live.sqlite3"
        self.snapshot = root / "upgrade"
        self.snapshot.mkdir()
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
            db.execute(
                "INSERT INTO meta VALUES (?, ?)",
                ("telegram_status_message_id", "101"),
            )
            db.execute(
                "INSERT INTO meta VALUES (?, ?)",
                ("telegram_status_media_identity", "text:-100123:101"),
            )
            db.commit()
        self.source = self.snapshot / "source.tar.gz"
        with tarfile.open(self.source, "w:gz") as archive:
            for name in FILES:
                content = b"source-fixture\n"
                info = tarfile.TarInfo(name)
                info.size = len(content)
                archive.addfile(info, io.BytesIO(content))
        seal_file(self.source)
        db_copy = root / "archive.sqlite3"
        with closing(sqlite3.connect(db_copy)) as db:
            db.execute("CREATE TABLE measurements(ts REAL, voltage REAL)")
            db.execute("INSERT INTO measurements VALUES (?, ?)", (10, 225))
            db.commit()
        self.archive = self.snapshot / "voltage-fixture.sqlite3.gz"
        with gzip.open(self.archive, "wb") as handle:
            handle.write(db_copy.read_bytes())
        seal_file(self.archive)

    def run_gate(self, *extras: str) -> subprocess.CompletedProcess:
        """Run against synthetic metadata without real Telegram tokens."""

        env = dict(os.environ)
        env["TEST_GRAPHIC_CHAT_ID"] = "-100123"
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--database",
                str(self.database),
                "--snapshot",
                str(self.snapshot),
                "--chat-env",
                "TEST_GRAPHIC_CHAT_ID",
                *extras,
            ],
            text=True,
            capture_output=True,
            env=env,
            check=False,
            timeout=25,
        )

    def test_verified_snapshot_is_ready_for_owner_not_deployment(self):
        """Passing file checks never imply automatic approval."""

        before = self.database.read_bytes()
        result = self.run_gate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("READY FOR OWNER REVIEW", result.stdout)
        self.assertIn("Owner Approval", result.stdout)
        self.assertEqual(before, self.database.read_bytes())

    def test_unverified_legacy_text_remains_blocked(self):
        """Even a fresh recovery snapshot cannot bless a legacy status."""

        with closing(sqlite3.connect(self.database)) as db:
            db.execute(
                "DELETE FROM meta WHERE key = ?",
                ("telegram_status_media_identity",),
            )
            db.commit()
        result = self.run_gate()
        self.assertEqual(result.returncode, 2)
        self.assertIn("identity is not verified", result.stdout)

    def test_timed_out_public_send_blocks_activation(self):
        """Unknown Telegram send outcomes demand human reconciliation."""

        with closing(sqlite3.connect(self.database)) as db:
            db.execute(
                "INSERT INTO meta VALUES (?, ?)",
                ("telegram_graphic_publish_pending", "-100123:101"),
            )
            db.commit()
        self.assertEqual(self.run_gate().returncode, 2)

    def test_stale_snapshot_is_not_accepted(self):
        """A past deployment snapshot cannot protect the next upgrade."""

        stale = time.time() - 5 * 3600
        os.utime(self.source, (stale, stale))
        result = self.run_gate("--max-snapshot-age-hours", "2")
        self.assertEqual(result.returncode, 2)
        self.assertIn("stale", result.stdout)

    def test_future_snapshot_is_not_accepted(self):
        """Bad clock or future archives cannot provide rollback proof."""

        future = time.time() + 3600
        os.utime(self.archive, (future, future))
        self.assertEqual(self.run_gate().returncode, 2)

    def test_invalid_snapshot_age_is_rejected(self):
        """Negative, zero, nan and extravagant ages do not disable checks."""

        for age in ("0", "-1", "nan", "9999"):
            with self.subTest(age=age):
                self.assertEqual(
                    self.run_gate("--max-snapshot-age-hours", age).returncode,
                    2,
                )

    def test_modified_archive_does_not_match_signed_sidecar(self):
        """One changed byte blocks a source rollback despite existing hash."""

        self.source.write_bytes(self.source.read_bytes() + b"tampered")
        result = self.run_gate()
        self.assertEqual(result.returncode, 2)
        self.assertIn("checksum mismatch", result.stdout)

    def test_corrupt_sqlite_archive_is_blocked_even_with_valid_checksum(self):
        """Successful SHA256 verification is not SQLite integrity proof."""

        self.archive.write_bytes(b"not-gzip")
        seal_file(self.archive)
        result = self.run_gate()
        self.assertEqual(result.returncode, 2)
        self.assertIn("SQLite archive is invalid", result.stdout)

    def test_corrupt_source_archive_is_blocked_even_with_valid_checksum(self):
        """A tar file with missing essential code cannot pass the gate."""

        self.source.write_bytes(b"not-gzip")
        seal_file(self.source)
        self.assertEqual(self.run_gate().returncode, 2)

    def test_linked_archive_and_sidecar_are_rejected(self):
        """Reject linked artifacts independently of file content."""

        sidecar = Path(str(self.archive) + ".sha256")
        original = sidecar.read_bytes()
        sidecar.unlink()
        source = self.snapshot.parent / "redirect.sha256"
        source.write_bytes(original)
        sidecar.symlink_to(source)
        self.assertEqual(self.run_gate().returncode, 2)

    def test_snapshot_with_extra_database_is_blocked(self):
        """Do not pick an arbitrary backup from ambiguous snapshots."""

        (self.snapshot / "voltage-extra.sqlite3.gz").write_bytes(b"other")
        self.assertEqual(self.run_gate().returncode, 2)

    def test_missing_snapshot_is_blocked(self):
        """Never create nonexistent snapshot directories during the check."""

        self.snapshot.rename(self.snapshot.with_name("moved"))
        self.assertEqual(self.run_gate().returncode, 2)
        self.assertFalse(self.snapshot.exists())

    def test_status_identifiers_never_appear_in_output(self):
        """The audit can be shared without revealing destination IDs."""

        result = self.run_gate()
        self.assertNotIn("-100123", result.stdout + result.stderr)
        self.assertNotIn(":101", result.stdout + result.stderr)

    def test_help_lists_noninteractive_options(self):
        """Operators can inspect exact arguments before any access."""

        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("--snapshot", result.stdout)
        self.assertIn("--max-snapshot-age-hours", result.stdout)


if __name__ == "__main__":
    unittest.main()
