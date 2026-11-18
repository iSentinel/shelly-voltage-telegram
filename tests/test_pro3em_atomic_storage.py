"""Keep three-phase transactions atomic and partial samples honest."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

from test_app import APP, config


def readings() -> dict[str, tuple[float, float, float]]:
    """Return one complete synthetic Pro3EM RPC response."""

    return {
        "L1": (214.0, 50.0, 3.0),
        "L2": (212.0, 50.0, 3.0),
        "L3": (207.0, 50.0, 3.0),
    }


class AtomicPro3EMStorageTests(unittest.TestCase):
    """Use only a private fixture with the real Storage schema."""

    def setUp(self) -> None:
        """Prepare one isolated SQLite WAL database."""

        folder = tempfile.TemporaryDirectory(prefix="svt-phase-atomic-")
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "synthetic.sqlite3"
        self.store = APP.Storage(self.path, "Europe/Kyiv")
        self.store.initialize()
        self.store.set_meta("telegram_setting_meter_type", "pro3em")
        self.store.set_meta("telegram_status_media_identity", "photo:1000")

    def phase_rows(self) -> list[tuple[str, float, float]]:
        """Retrieve saved phase, voltage and exact shared timestamp."""

        with closing(self.store.connect()) as db:
            self.assertEqual(
                db.execute("PRAGMA quick_check").fetchone()[0], "ok"
            )
            return db.execute(
                "SELECT phase, voltage, ts FROM measurements ORDER BY phase"
            ).fetchall()

    def test_full_triplet_commits_at_one_timestamp(self):
        """All three phases persist together using one timestamp."""

        self.store.add_phases(1800000000.0, readings())
        rows = self.phase_rows()
        self.assertEqual([row[0] for row in rows], ["L1", "L2", "L3"])
        self.assertEqual([row[1] for row in rows], [214, 212, 207])
        self.assertEqual({row[2] for row in rows}, {1800000000.0})

    def test_l3_sqlite_failure_rolls_back_l1_and_l2(self):
        """A failed final insert must undo every phase in the same poll."""

        with closing(self.store.connect()) as db, db:
            db.executescript(
                "CREATE TRIGGER reject_l3 BEFORE INSERT ON measurements "
                "WHEN NEW.phase='L3' "
                "BEGIN SELECT RAISE(ABORT,'synthetic L3 failure'); END;"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.add_phases(1800000000.0, readings())
        self.assertEqual(self.phase_rows(), [])
        self.assertEqual(
            self.store.get_meta("telegram_setting_meter_type"), "pro3em"
        )
        self.assertEqual(
            self.store.get_meta("telegram_status_media_identity"),
            "photo:1000",
        )

    def test_missing_l2_commits_only_real_l1_and_l3(self):
        """An unavailable phase is not invented as a zero-volt sample."""

        valid = readings()
        del valid["L2"]
        self.store.add_phases(1800000000.0, valid)
        self.assertEqual(
            [(row[0], row[1]) for row in self.phase_rows()],
            [("L1", 214.0), ("L3", 207.0)],
        )

    def test_empty_response_does_not_open_sqlite(self):
        """A total RPC failure cannot commit a phantom reading."""

        with patch.object(self.store, "connect") as conn:
            self.store.add_phases(1800000000.0, {})
            conn.assert_not_called()
        self.assertEqual(self.phase_rows(), [])

    def test_existing_single_phase_api_remains_compatible(self):
        """Keep the legacy L1-only commit path for older meters."""

        self.store.add(1800000000.0, 183.0, 49.8, "L1")
        self.assertEqual(
            [(row[0], row[1]) for row in self.phase_rows()],
            [("L1", 183.0)],
        )


class AtomicPro3EMPollTests(unittest.TestCase):
    """Verify actual Monitor phase/alert logic still receives valid samples."""

    def setUp(self) -> None:
        """Create an offline monitor with no Telegram credentials."""

        folder = tempfile.TemporaryDirectory(prefix="svt-phase-monitor-")
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "synthetic.sqlite3"
        self.bot = APP.Monitor(
            config(database_path=self.path, shelly_meter_type="pro3em")
        )
        self.bot.storage.initialize()
        self.bot.next_prune = APP.time.monotonic() + 3600

    def stored_phases(self) -> list[str]:
        """Return persisted phase labels without any network calls."""

        with closing(self.bot.storage.connect()) as db:
            return [
                r[0]
                for r in db.execute(
                    "SELECT phase FROM measurements ORDER BY phase"
                )
            ]

    def test_monitor_writes_exactly_one_batch_not_three_adds(self):
        """All values commit before phase notifications can run."""

        self.bot.shelly.read_phases = Mock(return_value=readings())
        with patch.object(self.bot.storage, "add") as add:
            self.assertTrue(self.bot.poll_pro3em_once())
            add.assert_not_called()
        self.assertEqual(self.stored_phases(), ["L1", "L2", "L3"])
        self.assertEqual(
            self.bot.phase_states["L2"].snapshot()["voltage"], 212
        )

    def test_missing_phase_still_marks_failure_not_zero_sample(self):
        """Partial hardware response retains legitimate phase data."""

        response = readings()
        del response["L2"]
        self.bot.shelly.read_phases = Mock(return_value=response)
        self.assertTrue(self.bot.poll_pro3em_once())
        self.assertEqual(self.stored_phases(), ["L1", "L3"])
        self.assertNotEqual(
            self.bot.phase_states["L2"].snapshot()["connectivity"],
            "online",
        )

    def test_failed_atomic_commit_does_not_publish_phase_states(self):
        """An SQL exception must happen before any phase status update."""

        self.bot.shelly.read_phases = Mock(return_value=readings())
        with closing(self.bot.storage.connect()) as db, db:
            db.executescript(
                "CREATE TRIGGER reject_l3 BEFORE INSERT ON measurements "
                "WHEN NEW.phase='L3' "
                "BEGIN SELECT RAISE(ABORT,'synthetic failure'); END;"
            )
        before = {
            p: self.bot.phase_states[p].snapshot()["connectivity"]
            for p in ("L1", "L2", "L3")
        }
        with self.assertRaises(sqlite3.IntegrityError):
            self.bot.poll_pro3em_once()
        self.assertEqual(self.stored_phases(), [])
        self.assertEqual(
            {
                p: self.bot.phase_states[p].snapshot()["connectivity"]
                for p in ("L1", "L2", "L3")
            },
            before,
        )


if __name__ == "__main__":
    unittest.main()
