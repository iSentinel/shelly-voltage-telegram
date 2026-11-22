"""Offline tests for fail-closed, complete Pro3EM recovery backups."""

from __future__ import annotations

import importlib.util
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from writer_fence import (
    WriterFenceError,
    quiescent_writer_lease,
    writer_lease,
)

SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts/fenced-newest-rescue.py"
)
SPEC = importlib.util.spec_from_file_location("svt_newest_rescue", SCRIPT)
assert SPEC and SPEC.loader
RESCUE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RESCUE)


class FencedNewestRescueTests(unittest.TestCase):
    """Use disposable SQLite, never real Shelly or Telegram resources."""

    def setUp(self) -> None:
        """Build a complete three-phase SQLite and PHOTO fixture."""
        tmp = tempfile.TemporaryDirectory(prefix="svt-fenced-rescue-")
        self.addCleanup(tmp.cleanup)
        self.source = Path(tmp.name) / "live.sqlite3"
        self.output = Path(tmp.name) / "newest.sqlite3"
        with closing(sqlite3.connect(self.source)) as db, db:
            db.executescript(
                "CREATE TABLE measurements ("
                "ts REAL, voltage REAL, frequency REAL, phase TEXT);"
                "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);"
                "INSERT INTO meta VALUES ('telegram_setting_meter_type',"
                " 'pro3em');"
                "INSERT INTO meta VALUES ('telegram_status_media_identity',"
                " 'photo:TEST');"
            )
            db.executemany(
                "INSERT INTO measurements VALUES (?,?,?,?)",
                [
                    (1000.0, 211.0, 50.0, "L1"),
                    (1000.0, 212.0, 50.0, "L2"),
                    (1000.0, 213.0, 50.0, "L3"),
                ],
            )

    def test_consistent_snapshot_is_logically_identical(self) -> None:
        """An SQLite backup copies all source tables and Telegram metadata."""
        expected = RESCUE.backup_consistently(self.source, self.output)
        with closing(sqlite3.connect(self.output)) as db:
            self.assertEqual(RESCUE.logical_digest(db), expected)
            RESCUE.check_sqlite(db)

    def test_missing_phase_is_rejected(self) -> None:
        """Partial phase histories cannot be promoted as full Pro3EM."""
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute("DELETE FROM measurements WHERE phase='L3'")
        with self.assertRaisesRegex(RESCUE.RescueBlocked, "MISSING_PHASES"):
            RESCUE.backup_consistently(self.source, self.output)

    def test_missing_photo_identity_is_rejected(self) -> None:
        """A backup with no stable PHOTO identity is not accepted."""
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute(
                "DELETE FROM meta WHERE key='telegram_status_media_identity'"
            )
        with self.assertRaisesRegex(RESCUE.RescueBlocked, "IDENTITY_MISMATCH"):
            RESCUE.backup_consistently(self.source, self.output)

    def test_changed_phase_remapping_changes_digest(self) -> None:
        """Row-count equality cannot conceal a changed L3 phase label."""
        with closing(sqlite3.connect(self.source)) as db:
            baseline = RESCUE.logical_digest(db)
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute("UPDATE measurements SET phase='L1' WHERE phase='L3'")
        with closing(sqlite3.connect(self.source)) as db:
            changed = RESCUE.logical_digest(db)
        self.assertNotEqual(changed, baseline)

    def test_source_hardlink_rejected(self) -> None:
        """Never copy SQLite via a hard-linked source path."""
        linked = self.source.with_name("linked.sqlite3")
        linked.hardlink_to(self.source)
        with self.assertRaisesRegex(RESCUE.RescueBlocked, "SOURCE_LINKED"):
            RESCUE.safe_paths(self.source, self.output)

    def test_source_symlink_rejected(self) -> None:
        """Never follow symbolic links as recovery inputs."""
        linked = self.source.with_name("linked.sqlite3")
        linked.symlink_to(self.source)
        with self.assertRaisesRegex(RESCUE.RescueBlocked, "SOURCE_INVALID"):
            RESCUE.safe_paths(linked, self.output)

    def test_atomic_rescue_publication_never_replaces_existing(self) -> None:
        """A destination created during staging must remain untouched."""
        from unittest.mock import patch

        self.output.write_bytes(b"unrelated-qa-data")
        with (
            patch.object(RESCUE, "safe_paths", return_value=None),
            self.assertRaises(FileExistsError),
        ):
            RESCUE.persist_backup(self.source, self.output)
        self.assertEqual(self.output.read_bytes(), b"unrelated-qa-data")

    def test_active_writer_prevents_quiescent_rescue(self) -> None:
        """A busy lifetime owner prevents updater's lease acquisition."""
        with (
            writer_lease(self.source),
            self.assertRaisesRegex(WriterFenceError, "SVT_WRITER_LOCK_BUSY"),
            quiescent_writer_lease(self.source),
        ):
            self.fail("second owner acquired a held lease")
        with quiescent_writer_lease(self.source):
            pass


if __name__ == "__main__":
    unittest.main()
