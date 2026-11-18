"""Check readiness and retention boundaries using isolated fixtures."""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from test_app import APP, config


class PhaseReadinessTests(unittest.TestCase):
    """Check each active phase while keeping the old one-phase contract."""

    def test_pro3em_waits_for_all_phases_to_recover(self):
        """L2 must finish recovery before the endpoint can return 200."""

        bot = APP.Monitor(config(shelly_meter_type="pro3em"))
        with patch.object(APP.time, "time", return_value=10000):
            for state in bot.phase_states.values():
                state.success(225, 50, 1)
            l2 = bot.phase_states["L2"]
            l2.error("synthetic failure")
            code, payload = APP.readiness(bot)
            self.assertEqual(code, 503)
            self.assertEqual(payload["unready_phases"], ["L2"])
            self.assertEqual(
                payload["phases"]["L2"]["connectivity"], "offline"
            )
            l2.success(225, 50, 1, recovery_after=2)
            code, payload = APP.readiness(bot)
            self.assertEqual(code, 503)
            self.assertEqual(payload["phases"]["L2"]["successes"], 1)
            l2.success(225, 50, 1, recovery_after=2)
            code, payload = APP.readiness(bot)
            self.assertEqual(code, 200)
            self.assertEqual(payload["unready_phases"], [])

    def test_stale_and_unmeasured_secondary_phases_block_readiness(self):
        """Fresh L1 cannot hide an old L2 or a missing L3."""

        bot = APP.Monitor(config(shelly_meter_type="pro3em"))
        with patch.object(APP.time, "time", return_value=1000):
            bot.phase_states["L2"].success(225, 50, 1)
        with patch.object(APP.time, "time", return_value=1100):
            bot.state.success(225, 50, 1)
            code, payload = APP.readiness(bot)
        self.assertEqual(code, 503)
        self.assertEqual(payload["unready_phases"], ["L2", "L3"])
        self.assertEqual(payload["phases"]["L2"]["reason"], "sample is stale")
        self.assertFalse(payload["phases"]["L3"]["ready"])

    def test_single_phase_response_is_compatible(self):
        """Pro EM-50 has one active line and the original JSON structure."""

        bot = APP.Monitor(config(shelly_meter_type="pro_em50"))
        with patch.object(APP.time, "time", return_value=10000):
            bot.state.success(225, 50, 1)
            code, payload = APP.readiness(bot)
        self.assertEqual(code, 200)
        self.assertEqual(
            payload, {"ready": True, "age": 0.0, "connectivity": "online"}
        )


class HourlyRollupTests(unittest.TestCase):
    """Protect complete aggregates at partial retention boundaries."""

    def setUp(self):
        """Create a disposable database with two separate UTC hours."""

        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.storage = APP.Storage(Path(folder.name) / "rollup.db", "UTC")
        self.storage.initialize()
        points = ((3600, 170), (5400, 230), (7100, 240), (7200, 220))
        for stamp, volts in points:
            self.storage.add(stamp, volts, 50)

    def aggregates(self):
        """Read the stored summaries for assertions."""
        with closing(self.storage.connect()) as db:
            return db.execute(
                "SELECT hour_ts,samples,min_v,max_v,avg_v "
                "FROM hourly_aggregates ORDER BY hour_ts"
            ).fetchall()

    def raw_times(self):
        """Read retained timestamps for assertions."""
        return [point[0] for point in self.storage.recent(10)]

    def test_prune_archives_and_deletes_only_closed_hours(self):
        """One cutoff at 01:30 retains all of the hour starting at 01:00."""

        with patch.object(APP.time, "time", return_value=86400 + 5400):
            self.assertEqual(self.storage.prune(1), 0)
        self.assertEqual(self.aggregates(), [])
        self.assertEqual(len(self.raw_times()), 4)
        with patch.object(APP.time, "time", return_value=86400 + 7300):
            self.assertEqual(self.storage.prune(1), 3)
        self.assertEqual(self.raw_times(), [7200])
        self.assertEqual(self.aggregates(), [(3600, 3, 170, 240, 640 / 3)])

    def test_new_rollup_preserves_aggregate_after_legacy_partial_prune(self):
        """A retained fragment cannot erase a historical low voltage."""

        self.storage.rollup_hourly(7200)
        with closing(self.storage.connect()) as db, db:
            db.execute("DELETE FROM measurements WHERE ts<5000")
        before = self.aggregates()
        self.storage.rollup_hourly(10800)
        self.assertEqual(self.aggregates()[0], before[0])
        self.assertEqual(self.aggregates()[1], (7200, 1, 220, 220, 220))

    def test_prune_failure_rolls_back_the_archive(self):
        """The archive and raw rows stay in sync if deletion fails."""

        with closing(self.storage.connect()) as db, db:
            db.execute(
                "CREATE TRIGGER reject_prune BEFORE DELETE ON measurements "
                "BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END;"
            )
        with (
            patch.object(APP.time, "time", return_value=86400 + 7300),
            self.assertRaises(sqlite3.IntegrityError),
        ):
            self.storage.prune(1)
        self.assertEqual(self.aggregates(), [])
        self.assertEqual(len(self.raw_times()), 4)
