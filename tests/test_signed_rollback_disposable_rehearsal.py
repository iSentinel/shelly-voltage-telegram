"""Disposable signed rollback failure and SQLite-history safety tests."""

from __future__ import annotations

import fcntl
import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/signed-rollback-disposable-rehearsal.py"
SPEC = importlib.util.spec_from_file_location(
    "signed_rollback_rehearsal", SCRIPT
)
assert SPEC is not None and SPEC.loader is not None
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)
OLD = "0.5.24-hw"
NEW = "0.5.25-hw"
DIGEST = "sha256:" + "a" * 64


class DisposableRollbackTests(unittest.TestCase):
    """Use generated private SQLite databases and synthetic OCI metadata."""

    def setUp(self) -> None:
        """Prepare one synthetic, private saved-image receipt."""

        temp = tempfile.TemporaryDirectory(prefix="svt-rollback-fixture-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.signed = self.root / "signed"
        self.signed.mkdir(mode=0o700)
        self.receipt = self.signed / "deployed.json"
        self.override = self.signed / "override.yaml"
        self.receipt.write_text(json.dumps({"version": OLD, "digest": DIGEST}))
        self.override.write_text(
            "services:\n  shelly-voltage:\n"
            "    image: ghcr.io/isentinel/shelly-voltage-telegram@"
            + DIGEST
            + "\n"
        )
        self.receipt.chmod(0o600)
        self.override.chmod(0o600)

    def directory(self, name: str) -> Path:
        """Create a unique, disposable transaction target."""

        target = self.root / name
        target.mkdir(mode=0o700)
        return target

    def phase_fixture(self, target: Path) -> tuple[str, int]:
        """Create synthetic post-cutover L1/L2/L3 plus saved Pro3EM type."""

        QA.create_fixture(target, 10)
        with closing(sqlite3.connect(target)) as db, db:
            db.executescript(
                "ALTER TABLE measurements ADD COLUMN phase TEXT "
                "NOT NULL DEFAULT 'L1';"
                "ALTER TABLE measurements ADD COLUMN frequency REAL "
                "NOT NULL DEFAULT 50.0;"
                "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);"
                "INSERT INTO meta VALUES ('telegram_setting_meter_type',"
                " 'pro3em');"
                "UPDATE measurements SET phase='L2' WHERE id IN (8,9);"
                "UPDATE measurements SET phase='L3' WHERE id=10;"
            )
        return QA.fixture_digest(target)

    def test_each_power_loss_boundary_preserves_new_history(self):
        """Never lose measurements, PHOTO ID or source/receipt parity."""

        for index, fault in enumerate(QA.FAULTS):
            with self.subTest(fault=fault):
                folder = self.directory(f"fault-{index}")
                outcome = QA.run_case(folder, self.signed, OLD, NEW, fault)
                self.assertEqual(outcome["preserved_measurements"], 10)
                self.assertTrue(outcome["photo_identity_preserved"])
                self.assertTrue(outcome["recovered"])
                self.assertFalse(outcome["bot_started"])
                self.assertEqual(QA.staged_identity(folder)[0], NEW)
                self.assertEqual(
                    json.loads((folder / "journal.json").read_text())["phase"],
                    "RECOVERED_NO_BOT_START",
                )

    def test_old_data_would_have_lost_four_measurements(self):
        """The fixture proves the old backup is not a current DB."""

        newer = self.root / "newer.sqlite3"
        older = self.root / "older.sqlite3"
        QA.create_fixture(newer, 10)
        QA.create_fixture(older, 6)
        self.assertNotEqual(QA.fixture_digest(newer), QA.fixture_digest(older))
        self.assertEqual(QA.fixture_digest(newer)[1], 10)

    def test_phase_change_with_identical_voltage_and_count_is_detected(self):
        """No rollback can disguise a lost L3 row as a current L1 row."""

        active = self.root / "newer-phases.sqlite3"
        rescue = self.root / "old-phases.sqlite3"
        current = self.phase_fixture(active)
        QA.sqlite_backup(active, rescue)
        with closing(sqlite3.connect(rescue)) as db, db:
            db.execute("UPDATE measurements SET phase='L1' WHERE id=10")
        self.assertEqual(QA.fixture_digest(rescue)[1], current[1])
        self.assertNotEqual(QA.fixture_digest(rescue)[0], current[0])

    def test_saved_meter_type_change_invalidates_full_history(self):
        """Old EM50 override cannot pass with the same voltage series."""

        active = self.root / "newer-config.sqlite3"
        rescue = self.root / "old-config.sqlite3"
        current = self.phase_fixture(active)
        QA.sqlite_backup(active, rescue)
        with closing(sqlite3.connect(rescue)) as db, db:
            db.execute(
                "UPDATE meta SET value='pro_em50' "
                "WHERE key='telegram_setting_meter_type'"
            )
        self.assertEqual(QA.fixture_digest(rescue)[1], current[1])
        self.assertNotEqual(QA.fixture_digest(rescue)[0], current[0])

    def test_stale_three_phase_rescue_refused_before_overwrite(self):
        """Preserve post-cutover phase rows when rescue is a stale image."""

        folder = self.directory("stale-three-phase")
        active, rescue = folder / "active.sqlite3", folder / "rescue.sqlite3"
        current = self.phase_fixture(active)
        QA.sqlite_backup(active, rescue)
        with closing(sqlite3.connect(rescue)) as db, db:
            db.execute("UPDATE measurements SET phase='L1' WHERE id=10")
        with self.assertRaisesRegex(QA.Blocked, "RESCUE_HISTORY_MISMATCH"):
            QA.recover_newer(folder, current[0], NEW)
        self.assertEqual(QA.fixture_digest(active), current)

    def test_three_phase_recovery_preserves_saved_model_and_photo(self):
        """Restore all new phases and config only from a verified rescue."""

        folder = self.directory("preserve-three-phase")
        active, rescue = folder / "active.sqlite3", folder / "rescue.sqlite3"
        current = self.phase_fixture(active)
        QA.sqlite_backup(active, rescue)
        with closing(sqlite3.connect(active)) as db, db:
            db.execute("UPDATE measurements SET phase='L1'")
            db.execute("UPDATE meta SET value='pro_em50'")
        QA.recover_fenced(folder, current[0], NEW)
        self.assertEqual(QA.fixture_digest(active), current)
        with closing(sqlite3.connect(active)) as db:
            phase_counts = db.execute(
                "SELECT phase,count(*) FROM measurements GROUP BY phase"
            ).fetchall()
            saved = db.execute("SELECT value FROM meta").fetchone()[0]
            photo = db.execute(
                "SELECT media_kind,message_id FROM telegram_status"
            ).fetchone()
        self.assertEqual(phase_counts, [("L1", 7), ("L2", 2), ("L3", 1)])
        self.assertEqual((saved, photo), ("pro3em", ("photo", 1000)))

    def test_corrupted_rescue_fails_before_database_overwrite(self):
        """Do not replace current records using a corrupt rescue image."""

        folder = self.directory("corrupt")
        active = folder / "active.sqlite3"
        rescued = folder / "rescue.sqlite3"
        QA.create_fixture(active, 10)
        QA.create_fixture(rescued, 6)
        original = QA.fixture_digest(active)
        with self.assertRaisesRegex(QA.Blocked, "RESCUE_HISTORY_MISMATCH"):
            QA.recover_newer(folder, original[0], NEW)
        self.assertEqual(QA.fixture_digest(active), original)

    def test_linked_restore_candidate_is_refused_before_active_swap(self):
        """A linked temporary SQLite file cannot redirect the recovery."""

        folder = self.directory("linked-candidate")
        active = folder / "active.sqlite3"
        rescued = folder / "rescue.sqlite3"
        QA.create_fixture(active, 6)
        QA.create_fixture(rescued, 10)
        old = QA.fixture_digest(active)
        newer = QA.fixture_digest(rescued)
        (folder / ".recovery-candidate.sqlite3").symlink_to(active)
        with self.assertRaisesRegex(
            QA.Blocked, "RECOVERY_CANDIDATE_PATH_UNSAFE"
        ):
            QA.recover_fenced(folder, newer[0], NEW)
        self.assertEqual(QA.fixture_digest(active), old)
        self.assertEqual(QA.fixture_digest(rescued), newer)

    def test_missing_rescue_fails_without_losing_current_history(self):
        """A missing rescue cannot be waved through as recovery."""

        folder = self.directory("missing")
        active = folder / "active.sqlite3"
        QA.create_fixture(active, 10)
        original = QA.fixture_digest(active)
        with self.assertRaisesRegex(QA.Blocked, "RESCUE_HISTORY_MISMATCH"):
            QA.recover_newer(folder, original[0], NEW)
        self.assertEqual(QA.fixture_digest(active), original)

    def test_writer_not_fenced_blocks_fixture_before_any_write(self):
        """A presumed running writer prevents the mock transaction."""

        folder = self.directory("unfenced")
        with (
            patch.dict(os.environ, {"SVT_DISPOSABLE_WRITER_FENCED": "false"}),
            self.assertRaisesRegex(QA.Blocked, "WRITER_NOT_FENCED"),
        ):
            QA.run_case(folder, self.signed, OLD, NEW, "after_old_db_swap")
        self.assertEqual(QA.fixture_digest(folder / "active.sqlite3")[1], 10)
        self.assertFalse((folder / "rescue.sqlite3").exists())

    def test_actual_contending_writer_lock_blocks_transaction(self):
        """A second fixture writer lease cannot be acquired concurrently."""

        folder = self.directory("locked")
        with (folder / "synthetic-writer.lock").open("w") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(QA.Blocked, "WRITER_NOT_FENCED"):
                QA.run_case(folder, self.signed, OLD, NEW, "after_old_db_swap")
        self.assertFalse((folder / "rescue.sqlite3").exists())

    def test_linked_writer_fence_path_is_rejected(self):
        """Symlink substitution never bypasses the disposable writer lock."""

        folder = self.directory("linked-fence")
        (folder / "synthetic-writer.lock").symlink_to(
            self.signed / "deployed.json"
        )
        with self.assertRaisesRegex(QA.Blocked, "WRITER_FENCE_PATH_UNSAFE"):
            QA.run_case(folder, self.signed, OLD, NEW, "after_rescue")

    def test_bad_old_receipt_cannot_be_staged_as_a_verified_pair(self):
        """The staged old version must agree with the saved receipt."""

        self.receipt.write_text(
            json.dumps({"version": "0.5.23-hw", "digest": DIGEST})
        )
        folder = self.directory("bad-receipt")
        with self.assertRaisesRegex(QA.Blocked, "STAGED_VERSION_MISMATCH"):
            QA.run_case(folder, self.signed, OLD, NEW, "after_old_db_swap")

    def test_modified_old_override_cannot_be_staged(self):
        """Reject the wrong old signed image pin before any swap."""

        self.override.write_text(
            "services:\n  shelly-voltage:\n    image: latest\n"
        )
        folder = self.directory("bad-override")
        with self.assertRaisesRegex(QA.Blocked, "STAGED_DIGEST_MISMATCH"):
            QA.run_case(folder, self.signed, OLD, NEW, "after_old_db_swap")
        self.assertEqual(QA.fixture_digest(folder / "active.sqlite3")[1], 10)

    def test_current_version_is_not_hard_coded(self):
        """Restore a synthetic newer target beyond the previous release."""

        folder = self.directory("future")
        outcome = QA.run_case(
            folder, self.signed, OLD, "0.5.26-hw", "after_old_db_swap"
        )
        self.assertTrue(outcome["recovered"])
        self.assertEqual(QA.staged_identity(folder)[0], "0.5.26-hw")

    def test_pair_rehearsal_never_grants_production_permission(self):
        """A guarded synthetic rehearsal is only an evidence receipt."""

        pair = SimpleNamespace(
            check_pair=lambda *args: {
                "source_version": OLD,
                "target_version": NEW,
                "signature_verified": True,
            }
        )
        with patch.object(QA, "load_pair", return_value=pair):
            outcome = QA.rehearse(self.root, self.signed, True)
        self.assertEqual(len(outcome["cases"]), 3)
        self.assertTrue(outcome["signed_identity_verified"])
        self.assertFalse(outcome["production_modified"])
        self.assertFalse(outcome["rollback_authorized"])
        self.assertFalse(outcome["release_authorized"])
        self.assertFalse(outcome["real_power_loss_tested"])
        self.assertTrue(outcome["no_real_bot_started"])
        self.assertTrue(outcome["no_telegram_api_calls"])

    def test_changed_photo_identity_blocks_rescue(self):
        """A matching row count cannot disguise a different PHOTO marker."""

        folder = self.directory("photo-tampering")
        active = folder / "active.sqlite3"
        rescue = folder / "rescue.sqlite3"
        QA.create_fixture(active, 10)
        QA.sqlite_backup(active, rescue)
        original = QA.fixture_digest(active)
        with closing(sqlite3.connect(rescue)) as database:
            database.execute(
                "UPDATE telegram_status SET message_id=2000 WHERE id=1"
            )
            database.commit()
        self.assertEqual(QA.fixture_digest(rescue)[1], 10)
        with self.assertRaisesRegex(QA.Blocked, "RESCUE_HISTORY_MISMATCH"):
            QA.recover_newer(folder, original[0], NEW)
        self.assertEqual(QA.fixture_digest(active), original)

    def test_unverified_pair_blocks_before_fixture_creation(self):
        """The signed identity guard runs before any simulated transaction."""

        pair = SimpleNamespace(
            check_pair=lambda *_args: (_ for _ in ()).throw(
                QA.Blocked("OLD_IMAGE_SIGNATURE_UNVERIFIED")
            )
        )
        with (
            patch.object(QA, "load_pair", return_value=pair),
            self.assertRaisesRegex(
                QA.Blocked, "OLD_IMAGE_SIGNATURE_UNVERIFIED"
            ),
        ):
            QA.rehearse(self.root, self.signed, True)
        self.assertFalse((self.root / "case-0").exists())

    def test_rescue_contains_photo_message_marker(self):
        """Backup and recovery protect PHOTO identity as well as rows."""

        folder = self.directory("photo")
        QA.run_case(folder, self.signed, OLD, NEW, "after_old_db_swap")
        with closing(sqlite3.connect(folder / "active.sqlite3")) as connection:
            entry = connection.execute(
                "SELECT media_kind, message_id FROM telegram_status"
            ).fetchone()
        self.assertEqual(entry, ("photo", 1000))


if __name__ == "__main__":
    unittest.main()
