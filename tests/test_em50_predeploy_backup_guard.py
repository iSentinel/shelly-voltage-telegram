"""Test exact predeployment SQLite backup parity without live writes."""

from __future__ import annotations

import contextlib
import gzip
import hashlib
import importlib.util
import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "em50_predeploy_guard", ROOT / "scripts/em50-predeploy-backup-guard.py"
)
assert SPEC is not None and SPEC.loader is not None
GATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GATE)


def make_db(path: Path, *, count: int = 3, status: str = "synthetic") -> None:
    """Create synthetic measurement/outage/status schema."""

    with contextlib.closing(sqlite3.connect(path)) as db:
        db.execute(
            "CREATE TABLE measurements "
            "(id INTEGER PRIMARY KEY,ts REAL,voltage REAL,"
            "frequency REAL,phase TEXT)"
        )
        db.execute(
            "CREATE TABLE outages "
            "(id INTEGER PRIMARY KEY,started_at REAL,ended_at REAL,phase TEXT)"
        )
        db.execute("CREATE TABLE meta (key TEXT,value TEXT)")
        db.executemany(
            "INSERT INTO measurements VALUES (?,?,?,?,?)",
            [(i, float(i), 218.0, 50.0, "L1") for i in range(1, count + 1)],
        )
        db.execute("INSERT INTO outages VALUES (18,?,?,?)", (1.0, 2.0, "L1"))
        db.execute(
            "INSERT INTO meta VALUES (?,?)",
            ("telegram_status_message_id", status),
        )
        db.commit()


def snapshot(db_path: Path, dest: Path) -> Path:
    """Create one versioned gzip archive and sidecar checksum."""

    archived = dest / "voltage-20261108-123000.sqlite3.gz"
    archived.write_bytes(gzip.compress(db_path.read_bytes()))
    digest = hashlib.sha256(archived.read_bytes()).hexdigest()
    Path(str(archived) + ".sha256").write_text(
        f"{digest}  {archived}\n", encoding="utf-8"
    )
    return archived


class PredeployBackupGuardTests(unittest.TestCase):
    """Never call an old or mismatched copy safe for an EM-50 rollout."""

    def setUp(self):
        """Create a private disposable backup separate from current DB."""

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "live.sqlite3"
        self.backup_source = self.root / "original.sqlite3"
        self.backups = self.root / "backups"
        self.backups.mkdir()
        make_db(self.db)
        make_db(self.backup_source)
        self.archive = snapshot(self.backup_source, self.backups)
        self.args = SimpleNamespace(
            db=str(self.db),
            backup_dir=str(self.backups),
            max_age_minutes=30.0,
        )
        self.now = time.time()

    def test_fresh_and_equal_is_pass_not_release_authorization(self):
        """An exact good backup may pass without authorizing a rollout."""

        report = GATE.inspect(self.args, self.now)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["record_gap"], 0)
        self.assertTrue(report["outage_state_matches"])
        self.assertTrue(report["status_metadata_matches"])
        self.assertFalse(report["release_authorized"])
        self.assertNotIn("synthetic", json.dumps(report))

    def test_read_only_does_not_change_current_database(self):
        """The guard only opens live SQLite in URI mode=ro."""

        before = self.db.read_bytes()
        GATE.inspect(self.args, self.now)
        self.assertEqual(self.db.read_bytes(), before)

    def test_stale_but_valid_backup_is_not_current_predeploy(self):
        """Valid SHA256 is necessary but does not prove freshness."""

        old = self.now - 2 * 3600
        os.utime(self.archive, (old, old))
        report = GATE.inspect(self.args, self.now)
        self.assertEqual(report["status"], "FAIL")
        self.assertIn("backup_stale", report["reason_codes"])
        self.assertEqual(report["backup_sha256"], "PASS")

    def test_missing_new_measurement_blocks_release(self):
        """A current SQLite history newer than backup must fail."""

        with contextlib.closing(sqlite3.connect(self.db)) as db:
            db.execute(
                "INSERT INTO measurements VALUES (?,?,?,?,?)",
                (4, 4.0, 216.0, 50.0, "L1"),
            )
            db.commit()
        report = GATE.inspect(self.args, self.now)
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["record_gap"], 1)
        self.assertIn("measurements_not_equal", report["reason_codes"])

    def test_same_count_but_different_last_measurement_fails(self):
        """Equal row count alone is not proof of restored-state parity."""

        with contextlib.closing(sqlite3.connect(self.db)) as db:
            db.execute("UPDATE measurements SET voltage=240 WHERE id=3")
            db.commit()
        report = GATE.inspect(self.args, self.now)
        self.assertEqual(report["record_gap"], 0)
        self.assertIn("measurements_not_equal", report["reason_codes"])

    def test_changed_older_reading_blocks_same_count_same_last(self):
        """Full history hash must detect changes hidden before last sample."""

        with contextlib.closing(sqlite3.connect(self.db)) as db:
            db.execute("UPDATE measurements SET voltage=232 WHERE id=1")
            db.commit()
        result = GATE.inspect(self.args, self.now)
        self.assertEqual(result["record_gap"], 0)
        self.assertTrue(result["latest_measurement_matches"])
        self.assertFalse(result["all_measurements_match"])
        self.assertIn("measurements_not_equal", result["reason_codes"])

    def test_changed_older_outage_blocks_same_latest_event(self):
        """A modified historical outage cannot be hidden by a newer event."""

        for file in (self.db, self.backup_source):
            with contextlib.closing(sqlite3.connect(file)) as db:
                db.execute(
                    "INSERT INTO outages VALUES (0,?,?,?)",
                    (0.1, 0.2, "L1"),
                )
                db.commit()
        snapshot(self.backup_source, self.backups)
        with contextlib.closing(sqlite3.connect(self.db)) as db:
            db.execute("UPDATE outages SET ended_at=0.3 WHERE id=0")
            db.commit()
        result = GATE.inspect(self.args, self.now)
        self.assertTrue(result["outage_state_matches"])
        self.assertFalse(result["all_outages_match"])
        self.assertIn("outage_state_not_equal", result["reason_codes"])

    def test_outage_closure_after_backup_fails(self):
        """Recovered incident records must exist in the new backup."""

        with contextlib.closing(sqlite3.connect(self.db)) as db:
            db.execute("UPDATE outages SET ended_at=10")
            db.commit()
        report = GATE.inspect(self.args, self.now)
        self.assertIn("outage_state_not_equal", report["reason_codes"])

    def test_status_message_rotation_after_backup_fails(self):
        """No status metadata content may be printed in the result."""

        with contextlib.closing(sqlite3.connect(self.db)) as db:
            db.execute(
                "UPDATE meta SET value='private_new_identifier' "
                "WHERE key='telegram_status_message_id'"
            )
            db.commit()
        report = GATE.inspect(self.args, self.now)
        self.assertIn(
            "status_message_metadata_not_equal", report["reason_codes"]
        )
        self.assertNotIn("private_new_identifier", json.dumps(report))

    def test_changed_non_status_meta_blocks_complete_backup(self):
        """A missing setting must not be hidden by matching outage data."""

        for file in (self.db, self.backup_source):
            with contextlib.closing(sqlite3.connect(file)) as db:
                db.execute(
                    "INSERT INTO meta VALUES (?,?)", ("ui_language", "uk")
                )
                db.commit()
        snapshot(self.backup_source, self.backups)
        with contextlib.closing(sqlite3.connect(self.db)) as db:
            db.execute(
                "UPDATE meta SET value=? WHERE key=?",
                ("ru", "ui_language"),
            )
            db.commit()
        result = GATE.inspect(self.args, time.time() + 1)
        self.assertIn("sqlite_state_not_equal", result["reason_codes"])
        self.assertNotIn("ui_language", json.dumps(result))

    def test_sqlite_user_version_header_blocks_mismatched_backup(self):
        """SQLite header migration marker is part of full backup parity."""

        with contextlib.closing(sqlite3.connect(self.db)) as db:
            db.execute("PRAGMA user_version=7")
        report = GATE.inspect(self.args, self.now)
        self.assertEqual(report["status"], "FAIL")
        self.assertIn("sqlite_state_not_equal", report["reason_codes"])

    def test_sqlite_application_id_header_blocks_mismatched_backup(self):
        """An application marker mismatch is not equivalent SQLite."""

        with contextlib.closing(sqlite3.connect(self.db)) as db:
            db.execute("PRAGMA application_id=123456")
        report = GATE.inspect(self.args, self.now)
        self.assertEqual(report["status"], "FAIL")
        self.assertIn("sqlite_state_not_equal", report["reason_codes"])

    def test_auxiliary_aggregate_changes_block_complete_backup(self):
        """Preserve all historical aggregate rows across an upgrade."""

        for file in (self.db, self.backup_source):
            with contextlib.closing(sqlite3.connect(file)) as db:
                db.execute(
                    "CREATE TABLE hourly_aggregates "
                    "(phase TEXT,hour_ts REAL,samples INTEGER)"
                )
                db.execute(
                    "INSERT INTO hourly_aggregates VALUES (?,?,?)",
                    ("L1", 10.0, 123),
                )
                db.commit()
        snapshot(self.backup_source, self.backups)
        with contextlib.closing(sqlite3.connect(self.db)) as db:
            db.execute("UPDATE hourly_aggregates SET samples=124")
            db.commit()
        result = GATE.inspect(self.args, time.time() + 1)
        self.assertIn("sqlite_state_not_equal", result["reason_codes"])

    def test_missing_settings_audit_schema_blocks_complete_backup(self):
        """Backup must contain all persisted user configuration tables."""

        with contextlib.closing(sqlite3.connect(self.db)) as db:
            db.execute(
                "CREATE TABLE settings_audit "
                "(id INTEGER PRIMARY KEY,ts REAL,setting TEXT)"
            )
            db.commit()
        result = GATE.inspect(self.args, self.now)
        self.assertIn("sqlite_state_not_equal", result["reason_codes"])

    def test_checksum_tampering_is_rejected(self):
        """Never unpack an archive whose digest does not match."""

        self.archive.write_bytes(self.archive.read_bytes() + b"tampered")
        with self.assertRaisesRegex(ValueError, "SHA256"):
            GATE.inspect(self.args, self.now)

    def test_checksum_wrong_path_is_rejected(self):
        """One checksum cannot authorize an unrelated archive path."""

        sidecar = Path(str(self.archive) + ".sha256")
        sidecar.write_text(
            "0" * 64 + "  /tmp/unrelated.sqlite3.gz\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "another file"):
            GATE.inspect(self.args, self.now)

    def test_expansion_has_hard_size_limit(self):
        """A compressed bomb must not fill disk during QA validation."""

        with (
            patch.object(GATE, "LIMIT_BYTES", 100),
            self.assertRaisesRegex(ValueError, "size limit"),
        ):
            GATE.inspect(self.args, self.now)

    def test_symlinked_archive_is_rejected(self):
        """Do not follow an archive symlink outside of backup directory."""

        link = self.backups / "voltage-9999.sqlite3.gz"
        link.symlink_to(self.archive)
        with self.assertRaisesRegex(ValueError, "ordinary file"):
            GATE.newest_backup(self.backups)

    def test_old_archive_symlink_is_rejected_before_sort(self):
        """Reject even a linked OLD candidate hidden behind a newer file."""

        old_source = self.root / "ancient.sqlite3.gz"
        old_source.write_bytes(b"synthetic")
        old = self.now - 86400
        os.utime(old_source, (old, old))
        old_link = self.backups / "voltage-1999.sqlite3.gz"
        old_link.symlink_to(old_source)
        with self.assertRaisesRegex(ValueError, "ordinary file"):
            GATE.newest_backup(self.backups)

    def test_decompressed_copy_is_never_world_readable(self):
        """Even inside a private directory, copied SQLite is mode 0600."""

        def inspect_mode(path: Path) -> dict:
            return {
                "file_mode": path.stat().st_mode & 0o777,
                "directory_mode": path.parent.stat().st_mode & 0o777,
            }

        with patch.object(GATE, "sqlite_state", side_effect=inspect_mode):
            result = GATE.unpack_state(self.archive)
        self.assertEqual(result["file_mode"], 0o600)
        self.assertEqual(result["directory_mode"], 0o700)

    def test_missing_live_sqlite_fails_without_creating_it(self):
        """Never create a synthetic production DB implicitly."""

        self.args.db = str(self.root / "nonexistent.sqlite3")
        with self.assertRaisesRegex(ValueError, "live SQLite missing"):
            GATE.inspect(self.args, self.now)
        self.assertFalse(Path(self.args.db).exists())

    def test_newer_backup_row_count_is_not_accepted(self):
        """Snapshot rows beyond current DB can indicate wrong backup."""

        with contextlib.closing(sqlite3.connect(self.db)) as db:
            db.execute("DELETE FROM measurements WHERE id=3")
            db.commit()
        report = GATE.inspect(self.args, self.now)
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["record_gap"], -1)


if __name__ == "__main__":
    unittest.main()
