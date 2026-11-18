"""Disposable signed rollback failure and SQLite-history safety tests."""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
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
        with sqlite3.connect(rescue) as database:
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
        with sqlite3.connect(folder / "active.sqlite3") as connection:
            entry = connection.execute(
                "SELECT media_kind, message_id FROM telegram_status"
            ).fetchone()
        self.assertEqual(entry, ("photo", 1000))


if __name__ == "__main__":
    unittest.main()
