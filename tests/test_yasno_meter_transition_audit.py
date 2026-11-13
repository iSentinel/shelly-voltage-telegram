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
INSERT_OUTAGE = "insert into outages(started_at,ended_at) values(?,?)"


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
            phase="L1",
            expect="X:O",
            minutes=15,
        )
        ddl = (
            "create table measurements ("
            "ts real, voltage real, frequency real, "
            "phase text not null default 'L1')"
        )
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute(ddl)
            db.execute(
                "create table outages (started_at real, ended_at real, "
                "phase text not null default 'L1')"
            )
            points = [
                (self.edge.timestamp() - 900 + index * 10, 223.5, 50.0)
                for index in range(180)
            ]
            db.executemany(
                "insert into measurements(ts,voltage,frequency) values(?,?,?)",
                points,
            )
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
        self.assertEqual(result["meter_voltage_continuity"], "CONFIRMED")
        self.assertEqual(
            result["forecast_vs_meter"],
            "PLANNED_RESTORATION_NOT_OBSERVED",
        )

    def test_planned_cut_does_not_override_real_voltage(self):
        """Scheduled X cannot claim a real outage amid valid meter data."""

        self.args.expect = "O:X"
        self.plan.bars = "O" * 8 + "X" + "O" * 39
        result = self.inspect(self.edge + timedelta(minutes=20))
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["meter_voltage_continuity"], "CONFIRMED")
        self.assertEqual(
            result["forecast_vs_meter"], "PLANNED_CUT_NOT_OBSERVED"
        )
        self.assertFalse(result["physical_outage_inferred_from_forecast"])
        self.assertEqual(
            (result["before_samples"], result["after_samples"]), (90, 90)
        )

    def test_other_phases_do_not_create_fake_l1_failures(self):
        """Overlapping L2/L3 readings and incidents cannot pollute L1."""

        with closing(sqlite3.connect(self.db)) as db, db:
            for phase in ("L2", "L3"):
                db.execute(
                    "insert into measurements "
                    "(ts,voltage,frequency,phase) values (?,?,?,?)",
                    (self.edge.timestamp(), 0.0, 50.0, phase),
                )
                db.execute(
                    "insert into outages "
                    "(started_at,ended_at,phase) values (?,?,?)",
                    (self.edge.timestamp(), self.edge.timestamp() + 40, phase),
                )
        result = self.inspect(self.edge + timedelta(minutes=20))
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["phase"], "L1")
        self.assertEqual(result["outages_overlap_window"], 0)
        self.assertEqual(
            (result["before_samples"], result["after_samples"]), (90, 90)
        )

    def test_selected_l2_uses_only_l2_data(self):
        """An explicit phase is read independently from L1 and L3."""

        self.args.phase = "L2"
        with closing(sqlite3.connect(self.db)) as db, db:
            points = [
                (self.edge.timestamp() - 900 + n * 10, 225.0, 50.0, "L2")
                for n in range(180)
            ]
            db.executemany(
                "insert into measurements "
                "(ts,voltage,frequency,phase) values (?,?,?,?)",
                points,
            )
        result = self.inspect(self.edge + timedelta(minutes=20))
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["phase"], "L2")
        self.assertEqual(
            (result["before_samples"], result["after_samples"]), (90, 90)
        )
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute(
                "insert into outages "
                "(started_at,ended_at,phase) values (?,?,?)",
                (self.edge.timestamp(), self.edge.timestamp() + 20, "L2"),
            )
        failed = self.inspect(self.edge + timedelta(minutes=20))
        self.assertEqual(failed["status"], "FAIL")
        self.assertEqual(failed["outages_overlap_window"], 1)
        self.assertEqual(failed["meter_voltage_continuity"], "UNVERIFIED")

    def test_other_phase_samples_cannot_fill_missing_l1_window(self):
        """A full L2 after-window never substitutes for missing L1."""

        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute(
                "delete from measurements where phase='L1' and ts>=?",
                (self.edge.timestamp(),),
            )
            points = [
                (self.edge.timestamp() + n * 10, 225.0, 50.0, "L2")
                for n in range(90)
            ]
            db.executemany(
                "insert into measurements "
                "(ts,voltage,frequency,phase) values (?,?,?,?)",
                points,
            )
        result = self.inspect(self.edge + timedelta(minutes=20))
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("INSUFFICIENT_SAMPLES", result["reasons"])
        self.assertEqual(result["after_samples"], 0)

    def test_future_window_is_pending_without_sqlite_read(self):
        """The forecast cannot substitute for future physical evidence."""

        future = clock(8, 30)
        self.args.boundary = future.isoformat()
        self.args.expect = "O:X"
        with mock.patch.object(AUDIT, "read_samples") as reader:
            result = self.inspect(clock(4, 45))
        self.assertEqual(result["status"], "PENDING")
        self.assertEqual(result["meter_voltage_continuity"], "UNVERIFIED")
        self.assertEqual(result["forecast_vs_meter"], "WINDOW_NOT_COMPLETE")
        self.assertEqual(
            result["observation_complete_kyiv"],
            clock(8, 45).isoformat(),
        )
        reader.assert_not_called()

    def test_concurrent_writer_does_not_split_read_snapshot(self):
        """Two SELECTs must observe the same SQLite WAL generation."""

        real_connect = sqlite3.connect
        database = self.db
        with closing(real_connect(database)) as setup:
            setup.execute("pragma journal_mode=WAL")

        class InsertingReader(sqlite3.Connection):
            """Insert an outage between the audited SELECT statements."""

            def execute(self, sql, *args, **kwargs):
                if sql.startswith("select count(*) from outages"):
                    with closing(real_connect(database)) as writer:
                        writer.execute(
                            INSERT_OUTAGE,
                            (self_edge, self_edge + 10),
                        )
                        writer.commit()
                return super().execute(sql, *args, **kwargs)

        self_edge = self.edge.timestamp()

        def open_reader(*args, **kwargs):
            """Supply an instrumented read-only connection."""
            return real_connect(*args, factory=InsertingReader, **kwargs)

        with mock.patch.object(
            AUDIT.sqlite3, "connect", side_effect=open_reader
        ):
            rows, observed = AUDIT.read_samples(
                database, self_edge - 900, self_edge + 900
            )

        with closing(real_connect(database)) as db:
            committed = db.execute("select count(*) from outages").fetchone()[
                0
            ]
        self.assertEqual((len(rows), observed, committed), (180, 0, 1))

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
        self.assertEqual(result["meter_voltage_continuity"], "UNVERIFIED")
        self.assertEqual(result["forecast_vs_meter"], "NOT_DETERMINED")

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
                INSERT_OUTAGE,
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
                INSERT_OUTAGE,
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
                "insert into measurements(ts,voltage,frequency) values(?,?,?)",
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
