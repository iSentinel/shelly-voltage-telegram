"""Post-hoc Shelly telemetry evidence must never imply Telegram delivery."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "posthoc_recovery",
    ROOT / "scripts/em50-posthoc-recovery-audit.py",
)
assert SPEC is not None and SPEC.loader is not None
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)

NOW = 3002.0


class PosthocRecoveryAuditTests(unittest.TestCase):
    """Allow verified measured recovery without a previously saved baseline."""

    def setUp(self) -> None:
        """Create a disposable SQLite outage and 10-second observations."""

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.db = Path(directory.name) / "telemetry.sqlite3"
        with contextlib.closing(sqlite3.connect(self.db)) as connection:
            connection.executescript(
                "create table outages(id integer primary key,"
                "started_at real,ended_at real,phase text);"
                "create table measurements(ts real,voltage real,"
                "frequency real,phase text);"
            )
            connection.execute("insert into outages values(18,1000,2000,'L1')")
            connection.executemany(
                "insert into measurements values(?,222,50,'L1')",
                [(stamp,) for stamp in range(2000, 3001, 10)],
            )
            connection.commit()

    def mutate(self, query: str) -> None:
        """Alter only a disposable fixture to simulate bad readings."""

        with contextlib.closing(sqlite3.connect(self.db)) as conn:
            conn.execute(query)
            conn.commit()

    def audit(self, now: float = NOW) -> dict:
        """Invoke the same telemetry-only database acceptance as CLI."""

        return QA.inspect_database(self.db, 18, now)

    def test_regular_15min_window_passes_without_telegram_claim(self):
        """Accept only actual phase samples and keep release blocked."""

        before = self.db.read_bytes()
        result = self.audit()
        self.assertEqual(result["status"], "pass")
        self.assertGreaterEqual(result["samples_15min"], 89)
        self.assertLessEqual(result["max_gap_seconds"], 90)
        self.assertEqual(result["scope"], "telemetry_only")
        self.assertFalse(result["telegram_delivery_verified"])
        self.assertFalse(result["release_authorized"])
        self.assertEqual(self.db.read_bytes(), before)

    def test_open_outage_fails(self):
        """An open incident cannot be called recovered."""

        self.mutate("update outages set ended_at=null where id=18")
        with self.assertRaisesRegex(ValueError, "still open"):
            self.audit()

    def test_other_later_outage_blocks_old_recovery(self):
        """Do not keep old PASS when another network loss has begun."""

        self.mutate("insert into outages values(19,2950,null,'L1')")
        with self.assertRaisesRegex(ValueError, "latest event"):
            self.audit()

    def test_wrong_incident_id_blocks(self):
        """Identity must be an explicit and exact SQLite incident ID."""

        with self.assertRaisesRegex(ValueError, "latest event"):
            QA.inspect_database(self.db, 17, NOW)

    def test_early_window_is_not_a_complete_pass(self):
        """No 15-minute claim is possible from only eight minutes."""

        with self.assertRaisesRegex(ValueError, "not complete"):
            self.audit(2500)

    def test_gap_over_ninety_seconds_blocks(self):
        """An unobserved interval is not stable physical telemetry."""

        self.mutate("delete from measurements where ts between 2320 and 2510")
        with self.assertRaisesRegex(ValueError, "incomplete"):
            self.audit()

    def test_wrong_phase_cannot_fill_missing_l1(self):
        """Other Shelly phase data cannot count toward L1 recovery."""

        self.mutate(
            "update measurements set phase='L2' where ts between 2330 and 2510"
        )
        with self.assertRaisesRegex(ValueError, "incomplete"):
            self.audit()

    def test_invalid_voltage_and_frequency_are_rejected(self):
        """Every original value in the audit window must be plausible."""

        for field, value in (("voltage", 0), ("frequency", 1)):
            with self.subTest(field=field):
                self.mutate(
                    f"update measurements set {field}={value} where ts=2440"
                )
                with self.assertRaisesRegex(ValueError, "invalid"):
                    self.audit()
                self.mutate(
                    f"update measurements set {field}="
                    f"{222 if field == 'voltage' else 50} where ts=2440"
                )

    def test_latest_sample_stale_even_if_window_good(self):
        """Historical valid samples cannot prove current online state."""

        self.mutate("delete from measurements where ts>2900")
        with self.assertRaisesRegex(ValueError, "stale"):
            self.audit()

    def test_latest_sample_invalid_even_if_window_good(self):
        """Reject an invalid newest reading rather than an older one."""

        self.mutate("update measurements set voltage=0 where ts=3000")
        with self.assertRaisesRegex(ValueError, "invalid latest"):
            self.audit()

    def test_future_dated_rows_block(self):
        """Clock jumps or fake future samples must not pass."""

        self.mutate("insert into measurements values(3100,222,50,'L1')")
        with self.assertRaises(ValueError):
            self.audit()

    def test_zero_rows_on_other_phase_block(self):
        """Do not hide synthetic voltage zeros on other phases."""

        self.mutate("insert into measurements values(2100,0,50,'L2')")
        with self.assertRaisesRegex(ValueError, "synthetic zero"):
            self.audit()

    def test_invalid_incident_closure_blocks(self):
        """Closed timestamp cannot predate incident start."""

        self.mutate("update outages set ended_at=900 where id=18")
        with self.assertRaisesRegex(ValueError, "timestamp invalid"):
            self.audit()

    def test_missing_or_linked_database_blocks(self):
        """Do not open arbitrary linked or missing database paths."""

        link = self.db.with_name("linked.sqlite3")
        link.symlink_to(self.db)
        for path in (link, self.db.with_name("missing.sqlite3")):
            with (
                self.subTest(path=path.name),
                self.assertRaises(ValueError),
            ):
                QA.open_readonly(path)

    def test_cli_never_reports_full_acceptance(self):
        """Even a passing CLI report is only physical telemetry evidence."""

        args = [
            "audit.py",
            "--db",
            str(self.db),
            "--incident-id",
            "18",
            "--json",
        ]
        with (
            patch.object(sys, "argv", args),
            patch.object(QA.time, "time", return_value=NOW),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(QA.main(), 0)
        report = json.loads(output.getvalue())
        self.assertEqual(report["status"], "pass")
        self.assertFalse(report["telegram_delivery_verified"])
        self.assertFalse(report["release_authorized"])

    def test_bad_cli_event_is_blocked_without_leaking_sqlite(self):
        """Unknown incident gives only safe status, never DB contents."""

        args = [
            "audit.py",
            "--db",
            str(self.db),
            "--incident-id",
            "19",
            "--json",
        ]
        with (
            patch.object(sys, "argv", args),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(QA.main(), 1)
        report = json.loads(output.getvalue())
        self.assertEqual(report["status"], "blocked")
        self.assertFalse(report["release_authorized"])


if __name__ == "__main__":
    unittest.main()
