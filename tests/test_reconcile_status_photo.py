"""Test an in-place Telegram status recovery using an isolated SQLite WAL."""

import argparse
import importlib.util
import json
import sqlite3
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from scripts import github_update

SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts/reconcile-status-photo.py"
)
SPEC = importlib.util.spec_from_file_location("svt_status_repair", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
REPAIR = importlib.util.module_from_spec(SPEC)
with patch.dict(sys.modules, {"github_update": github_update}):
    SPEC.loader.exec_module(REPAIR)

PHASES = ("L1", "L2", "L3")
PHOTO = "1222"


class StatusReconciliationTests(unittest.TestCase):
    """Preserve the live message and its history while retiring old intent."""

    def setUp(self) -> None:
        """Create a settled episode with committed rows still in the WAL."""
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        root = Path(work.name)
        self.db = root / "monitor.sqlite3"
        self.backup = root / "before.sqlite3"
        self.open = sqlite3.connect(self.db, timeout=2)
        self.addCleanup(self.open.close)
        self.open.executescript(
            "PRAGMA journal_mode=WAL; PRAGMA wal_autocheckpoint=0;"
            "CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);"
            "CREATE TABLE outages(ended_at REAL);"
            "CREATE TABLE measurements(ts REAL, voltage REAL, phase TEXT);"
        )
        now = time.time()
        recovered = now - 1200
        self.meta = self.settled_metadata(now, recovered)
        with self.open:
            self.open.executemany(
                "INSERT INTO meta VALUES (?, ?)", self.meta.items()
            )
            self.open.executemany(
                "INSERT INTO measurements VALUES (?, 222, ?)",
                ((now, phase) for phase in PHASES),
            )

    def settled_metadata(self, now: float, recovered: float) -> dict:
        """Return metadata for a matched and settled recovery fixture."""
        return {
            "telegram_status_message_id": PHOTO,
            "telegram_status_media_identity": f"photo:-101:{PHOTO}",
            "telegram_graphic_publish_pending": f"-101:{PHOTO}",
            "telegram_recovery_rotation_pending_at": str(now - 3600),
            "voltage_recovery_stable_since": str(recovered),
            "voltage_status_edit_window_started_at": str(now - 1350),
            "last_voltage_event": json.dumps(
                {
                    "kind": "recovered",
                    "phase": "L2",
                    "timestamp": recovered,
                }
            ),
            "voltage_episode": json.dumps(
                {
                    "last_at": recovered,
                    "finalized_at": recovered + 450,
                    "transitions": [{"kind": "recovered", "at": recovered}],
                }
            ),
            "voltage_transition_history": '{"history":"keep"}',
            **{
                f"voltage_detector_{phase}": '{"level":"normal","low":false}'
                for phase in PHASES
            },
        }

    def repair(self, fingerprint: str | None = None) -> None:
        """Act on the fixture, substituting only the systemd fence check."""
        args = argparse.Namespace(
            db=self.db,
            confirmed_photo=PHOTO,
            backup=self.backup,
            expect_fingerprint=fingerprint
            or REPAIR.inspect(self.open, PHOTO, PHASES),
        )
        with patch.object(REPAIR, "require_stopped_writers"):
            REPAIR.commit_repair(args, PHASES)

    def test_preserves_photo_and_wal_history(self) -> None:
        """A verified repair preserves committed rows and audit metadata."""
        self.repair()
        kept = self.meta.keys() - set(REPAIR.RETIRE_KEYS)
        expected = {key: self.meta[key] for key in kept}
        self.assertEqual(
            dict(self.open.execute("SELECT * FROM meta")), expected
        )
        with closing(sqlite3.connect(self.backup)) as archived:
            self.assertEqual(
                dict(archived.execute("SELECT * FROM meta")), self.meta
            )
            sql = "SELECT * FROM measurements ORDER BY phase"
            self.assertEqual(
                archived.execute(sql).fetchall(),
                self.open.execute(sql).fetchall(),
            )
        self.assertEqual(self.backup.stat().st_mode & 0o777, 0o600)

    def test_refuses_different_binding(self) -> None:
        """An inspection cannot use a different message or omit phases."""
        with self.assertRaisesRegex(ValueError, "stored status ID"):
            REPAIR.inspect(self.open, "1223", PHASES)
        with self.assertRaisesRegex(ValueError, "stored detectors"):
            REPAIR.inspect(self.open, PHOTO, ("L1",))
        self.assertFalse(self.backup.exists())

    def test_refuses_stale_fingerprint(self) -> None:
        """A previously inspected version cannot replace changed state."""
        fingerprint = REPAIR.inspect(self.open, PHOTO, PHASES)
        with self.open:
            self.open.execute(
                "UPDATE meta SET value=? WHERE key=?",
                (
                    str(time.time() - 2),
                    "telegram_recovery_rotation_pending_at",
                ),
            )
        with self.assertRaisesRegex(ValueError, "changed since inspection"):
            self.repair(fingerprint)
        self.assertFalse(self.backup.exists())
        self.assertIn(
            "voltage_episode", dict(self.open.execute("SELECT * FROM meta"))
        )

    def test_refuses_to_replace_backup(self) -> None:
        """Protect an existing backup and leave the original state intact."""
        self.backup.write_bytes(b"earlier rescue")
        with self.assertRaises(FileExistsError):
            self.repair()
        self.assertEqual(self.backup.read_bytes(), b"earlier rescue")
        self.assertEqual(
            dict(self.open.execute("SELECT * FROM meta")), self.meta
        )

    def test_refuses_active_writer(self) -> None:
        """An active writer aborts before opening a repair snapshot."""
        args = argparse.Namespace(
            db=self.db,
            confirmed_photo=PHOTO,
            backup=self.backup,
            expect_fingerprint=REPAIR.inspect(self.open, PHOTO, PHASES),
        )
        with (
            patch.object(
                REPAIR,
                "require_stopped_writers",
                side_effect=ValueError("running"),
            ),
            self.assertRaisesRegex(ValueError, "running"),
        ):
            REPAIR.commit_repair(args, PHASES)
        self.assertFalse(self.backup.exists())
