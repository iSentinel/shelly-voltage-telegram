"""Verify strict read-only physical evidence around forecast transitions."""

from __future__ import annotations

import importlib.util
import sqlite3
import tempfile
import unittest
from argparse import Namespace
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "yasno-meter-transition-audit.py"
SPEC = importlib.util.spec_from_file_location("meter_transition_audit", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)
KYIV = ZoneInfo("Europe/Kyiv")
BARS = "XXXXXXXXOOOOOOOOOXXXXXXXXXXXXOOOOXXXXXXXXXXXOOOO"


def clock(hour: int, minute: int = 0) -> datetime:
    """Make a local Nov 12 timestamp with daylight-saving rules."""

    return datetime(2026, 11, 12, hour, minute, tzinfo=KYIV)


class MeterTransitionAuditTests(unittest.TestCase):
    """Exercise prospective and completed windows without public mutations."""

    def setUp(self):
        """Prepare an isolated physically plausible SQLite time series."""

        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.db = Path(folder.name) / "monitor.sqlite3"
        self.edge = clock(4)
        self.args = Namespace(
            database=self.db,
            yasno_cache=Path(folder.name) / "trusted.json",
            boundary=self.edge.isoformat(),
            group="10.1",
            expect="X:O",
            minutes=15,
        )
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute(
                "create table measurements ("
                "ts real, voltage real, frequency real)"
            )
            db.execute("create table outages (started_at real, ended_at real)")
            points = [
                (self.edge.timestamp() - 900 + index * 10, 223.5, 50.0)
                for index in range(180)
            ]
            db.executemany("insert into measurements values (?, ?, ?)", points)
        self.plan = SimpleNamespace(
            bars=BARS, day="2026-11-12", group="10.1", source="yasno-kyiv-dtek"
        )

    def inspect(self, now: datetime) -> dict:
        """Inject verified forecast while retaining the real SQLite code."""

        with (
            mock.patch.object(
                AUDIT, "read_verified_cache", return_value=({}, now)
            ),
            mock.patch.object(AUDIT, "parse_schedule", return_value=self.plan),
        ):
            return AUDIT.inspect(self.args, now)

    def test_complete_window_is_verified_read_only(self):
        """A full verified physical window reports PASS without writes."""

        result = self.inspect(self.edge + timedelta(minutes=20))
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["before_samples"], 90)
        self.assertEqual(result["after_samples"], 90)
        self.assertEqual(result["forecast_before"], "X")
        self.assertEqual(result["forecast_after"], "O")
        self.assertFalse(result["telegram_mutated"])
        self.assertFalse(result["writes_performed"])

    def test_future_window_is_pending_without_sqlite_read(self):
        """The forecast cannot substitute for future physical evidence."""

        future = clock(8, 30)
        self.args.boundary = future.isoformat()
        self.args.expect = "O:X"
        with mock.patch.object(AUDIT, "read_samples") as reader:
            result = self.inspect(clock(4, 45))
        self.assertEqual(result["status"], "PENDING")
        self.assertEqual(
            result["observation_complete_kyiv"],
            clock(8, 45).isoformat(),
        )
        reader.assert_not_called()

    def test_missing_samples_fail_closed(self):
        """Insufficient physical samples cannot masquerade as continuity."""

        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute(
                "delete from measurements where ts >= ?",
                (self.edge.timestamp(),),
            )
        result = self.inspect(self.edge + timedelta(minutes=20))
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("INSUFFICIENT_SAMPLES", result["reasons"])

    def test_large_sample_gap_fails_strict_30_second_gate(self):
        """A physical sample gap greater than 30 seconds rejects PASS."""

        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute(
                "delete from measurements where ts >= ? and ts <= ?",
                (self.edge.timestamp() - 20, self.edge.timestamp() + 20),
            )
        result = self.inspect(self.edge + timedelta(minutes=20))
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("MEASUREMENT_GAP", result["reasons"])

    def test_zero_sample_fails_without_claiming_schedule_caused_it(self):
        """A zero voltage sample is physical data, not a YASNO decision."""

        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute(
                "update measurements set voltage = 0 where ts = ?",
                (self.edge.timestamp(),),
            )
        result = self.inspect(self.edge + timedelta(minutes=20))
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("INVALID_PHYSICAL_SAMPLE", result["reasons"])
        self.assertFalse(result["physical_outage_inferred_from_forecast"])

    def test_null_numeric_fields_are_safely_rejected(self):
        """Corrupt SQLite voltage must fail instead of raising TypeError."""

        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute(
                "update measurements set voltage = null where ts = ?",
                (self.edge.timestamp(),),
            )
        with self.assertRaisesRegex(ValueError, "INVALID_PHYSICAL_SAMPLE"):
            self.inspect(self.edge + timedelta(minutes=20))

    def test_non_numeric_frequency_is_safely_rejected(self):
        """Text frequency cannot enter a successful physical audit."""

        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute(
                "update measurements set frequency = 'bad' where ts = ?",
                (self.edge.timestamp(),),
            )
        with self.assertRaisesRegex(ValueError, "INVALID_PHYSICAL_SAMPLE"):
            self.inspect(self.edge + timedelta(minutes=20))

    def test_recorded_outage_fails_continuity_acceptance(self):
        """An outage record is an independent physical acceptance failure."""

        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute(
                "insert into outages values (?, ?)",
                (self.edge.timestamp(), self.edge.timestamp() + 20),
            )
        result = self.inspect(self.edge + timedelta(minutes=20))
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("REAL_OUTAGE_RECORDED", result["reasons"])

    def test_outage_started_before_window_is_detected(self):
        """An outage crossing the left edge must not be overlooked."""

        start = self.edge.timestamp() - 901
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute(
                "insert into outages values (?, ?)",
                (start, start + 20),
            )
        result = self.inspect(self.edge + timedelta(minutes=20))
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("REAL_OUTAGE_RECORDED", result["reasons"])
        self.assertEqual(result["outages_overlap_window"], 1)

    def test_repeated_sample_timestamp_does_not_fake_continuity(self):
        """Duplicate timestamps cannot inflate independent sample counts."""

        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute(
                "insert into measurements values (?, ?, ?)",
                (self.edge.timestamp(), 223.5, 50.0),
            )
        result = self.inspect(self.edge + timedelta(minutes=20))
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("REPEATED_TIMESTAMP", result["reasons"])

    def test_forecast_mismatch_fails_even_with_perfect_samples(self):
        """Require the selected real YASNO transition, not a guessed edge."""

        self.args.expect = "O:X"
        with self.assertRaisesRegex(
            ValueError, "FORECAST_TRANSITION_MISMATCH"
        ):
            self.inspect(self.edge + timedelta(minutes=20))

    def test_invalid_time_and_day_fail_closed(self):
        """Reject UTC-offset confusion, missing offset, and wrong day."""

        self.args.boundary = "2026-11-12T04:00:00"
        with self.assertRaisesRegex(ValueError, "BOUNDARY_TZ_MISSING"):
            self.inspect(self.edge + timedelta(minutes=20))
        self.args.boundary = "2026-11-12T04:00:00+03:00"
        with self.assertRaisesRegex(
            ValueError, "BOUNDARY_KYIV_OFFSET_MISMATCH"
        ):
            self.inspect(self.edge + timedelta(minutes=20))
        self.args.boundary = clock(4).isoformat()
        with self.assertRaisesRegex(ValueError, "BOUNDARY_NOT_TODAY"):
            self.inspect(clock(5) + timedelta(days=1))

    def test_unsafe_sqlite_symlink_is_rejected(self):
        """Refuse non-regular or linked database paths."""

        alias = self.db.with_suffix(".link")
        alias.symlink_to(self.db)
        self.args.database = alias
        with self.assertRaisesRegex(ValueError, "DATABASE_UNSAFE"):
            self.inspect(self.edge + timedelta(minutes=20))


if __name__ == "__main__":
    unittest.main()
