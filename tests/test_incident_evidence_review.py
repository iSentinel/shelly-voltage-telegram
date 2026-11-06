"""Offline acceptance for independently confirmed electricity outages."""

from __future__ import annotations

import importlib.util
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts/incident-evidence-review.py"
)
SPEC = importlib.util.spec_from_file_location("incident_review", SCRIPT)
REVIEW = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REVIEW)

LOSS = "2026-11-06T20:31:07.098988+02:00"
SAMPLE = "2026-11-06T20:30:25+02:00"
REPORT = "2026-11-06T20:33:06+02:00"


def epoch(value: str) -> float:
    """Return an exact timezone-aware fixture timestamp."""

    return datetime.fromisoformat(value).timestamp()


class IndependentIncidentEvidenceTests(unittest.TestCase):
    """Require independent user confirmation to assert a power outage."""

    def setUp(self):
        """Build a disposable SQLite incident and private evidence document."""

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        self.db = base / "monitor.sqlite3"
        self.file = base / "incident.json"
        self.record = {
            "incident": "natural incident #15",
            "independent_evidence": {
                "actual_power_off": "user_confirmed",
                "exact_power_off_timestamp": None,
                "user_confirmation_received_by_kyiv": REPORT,
            },
            "observed_evidence": {
                "shelly_connection_loss_started_kyiv": LOSS,
            },
            "forecast": {
                "date": "2026-11-06",
                "group": "10.1",
                "source": "user_supplied_unverified",
                "provider_crosschecked": False,
                "approximate_window": ["20:00", "24:00"],
            },
        }
        self.file.write_text(json.dumps(self.record))
        self.create_db()

    def create_db(self):
        """Store isolated outages and measurements for the test fixture."""

        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute(
                "create table outages (id integer primary key, "
                "started_at real, ended_at real, phase text)"
            )
            connection.execute(
                "create table measurements (ts real, voltage real, "
                "frequency real)"
            )
            connection.execute(
                "insert into outages values (?,?,?,?)",
                (15, epoch(LOSS), None, "L1"),
            )
            connection.execute(
                "insert into measurements values (?,?,?)",
                (epoch(SAMPLE), 234.8, 50.0),
            )

    def run_review(self):
        """Use the same read-only functions as the public CLI entrypoint."""

        document = REVIEW.read_incident(self.file)
        event, sample = REVIEW.query_incident(self.db, 15)
        return REVIEW.review(document, event, sample)

    def write_record(self):
        """Commit a changed synthetic source document only to test storage."""

        self.file.write_text(json.dumps(self.record))

    def test_open_incident_has_independent_confirmation(self):
        """A user report is different evidence from a Shelly network gap."""

        result = self.run_review()
        self.assertTrue(result["power_off_reported_by_operator"])
        self.assertIsNone(result["exact_power_off_time"])
        self.assertEqual(result["incident_id"], 15)
        self.assertIsNone(result["connection_restored_kyiv"])
        self.assertEqual(result["last_real_voltage_before_loss_v"], 234.8)
        self.assertTrue(result["read_only"])

    def test_open_gap_duration_not_claimed_as_power_off_duration(self):
        """An extended offline gap cannot measure real mains downtime."""

        clock = epoch("2026-11-06T21:06:07+02:00")
        record = REVIEW.read_incident(self.file)
        row, last = REVIEW.query_incident(self.db, 15)
        result = REVIEW.review(record, row, last, observed_at=clock)
        self.assertEqual(result["connection_gap_state"], "open")
        self.assertAlmostEqual(
            result["connection_gap_elapsed_seconds"], 2100.0, delta=0.2
        )
        self.assertIsNone(result["actual_power_off_duration_seconds"])
        self.assertIsNone(result["current_voltage_v"])
        self.assertEqual(
            result["current_voltage_status"],
            "unknown_while_connection_lost",
        )

    def test_last_sample_age_is_relative_to_network_loss(self):
        """Avoid displaying a pre-gap reading as a current voltage."""

        result = self.run_review()
        self.assertAlmostEqual(
            result["last_sample_age_at_loss_seconds"], 42.1, delta=0.2
        )
        self.assertIsNone(result["current_voltage_v"])
        self.assertFalse(result["current_voltage_assumed_zero"])

    def test_closed_event_stays_historical_not_current(self):
        """Closing Shelly connectivity does not verify today's voltage."""

        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute(
                "update outages set ended_at=? where id=15",
                (epoch("2026-11-06T21:02:00+02:00"),),
            )
        record = REVIEW.read_incident(self.file)
        event, last = REVIEW.query_incident(self.db, 15)
        result = REVIEW.review(
            record, event, last, epoch("2026-11-06T21:03:00+02:00")
        )
        self.assertEqual(result["connection_gap_state"], "closed")
        self.assertEqual(result["connection_gap_elapsed_seconds"], 1852.9)
        self.assertEqual(
            result["current_voltage_status"], "not_verified_by_review"
        )
        self.assertIsNone(result["actual_power_off_duration_seconds"])

    def test_review_rejects_time_before_connection_loss(self):
        """Never calculate a negative elapsed duration for an open gap."""

        record = REVIEW.read_incident(self.file)
        event, last = REVIEW.query_incident(self.db, 15)
        with self.assertRaisesRegex(ValueError, "predates"):
            REVIEW.review(
                record, event, last, epoch("2026-11-06T20:30:59+02:00")
            )

    def test_russian_offline_report_hides_stale_voltage(self):
        """Operator wording must say unknown rather than the old 234.8 V."""

        result = self.run_review()
        message = REVIEW.operator_text(result, "ru")
        self.assertIn("Текущее напряжение неизвестно", message)
        self.assertNotIn("234.8", message)

    def test_naive_observation_cli_is_fail_closed(self):
        """A timestamp without timezone must not use the host default."""

        args = [
            "incident-evidence-review",
            "--incident",
            str(self.file),
            "--db",
            str(self.db),
            "--at",
            "2026-11-06T21:00:00",
            "--json",
        ]
        output = io.StringIO()
        with patch.object(sys, "argv", args), redirect_stdout(output):
            self.assertEqual(REVIEW.main(), 1)
        self.assertEqual(json.loads(output.getvalue())["status"], "fail")

    def test_planned_window_does_not_verify_cause(self):
        """A matching forecast cannot prove exact power-switch timing."""

        result = self.run_review()
        self.assertTrue(result["gap_began_in_planned_window"])
        self.assertTrue(result["schedule_is_approximate"])
        self.assertFalse(result["cause_verified_by_schedule"])
        self.assertFalse(result["current_voltage_assumed_zero"])

    def test_user_can_report_before_shelly_is_marked_offline(self):
        """A delayed third failed poll must not invalidate the user report."""

        self.record["independent_evidence"][
            "user_confirmation_received_by_kyiv"
        ] = "2026-11-06T20:30:35+02:00"
        self.write_record()
        self.assertTrue(self.run_review()["power_off_reported_by_operator"])

    def test_missing_confirmation_fails_closed(self):
        """A connectivity gap alone is not an outage confirmation."""

        self.record["independent_evidence"]["actual_power_off"] = "unknown"
        self.write_record()
        with self.assertRaisesRegex(ValueError, "confirmed"):
            REVIEW.read_incident(self.file)

    def test_forged_exact_off_timestamp_rejected(self):
        """Do not promote a network timeout to an exact mains-off time."""

        self.record["independent_evidence"]["exact_power_off_timestamp"] = LOSS
        self.write_record()
        with self.assertRaisesRegex(ValueError, "unknown"):
            REVIEW.read_incident(self.file)

    def test_unverified_schedule_cannot_claim_official_data(self):
        """The operator forecast must remain an unverified manual source."""

        self.record["forecast"]["provider_crosschecked"] = True
        self.write_record()
        with self.assertRaisesRegex(ValueError, "cross-checked"):
            REVIEW.read_incident(self.file)

    def test_mismatching_shelly_event_rejected(self):
        """An incident snapshot may not silently describe another event."""

        self.record["observed_evidence"][
            "shelly_connection_loss_started_kyiv"
        ] = "2026-11-06T20:15:00+02:00"
        self.write_record()
        with self.assertRaisesRegex(ValueError, "timeline"):
            self.run_review()

    def test_bad_voltage_not_used_as_real_measurement(self):
        """A fabricated zero row is not a valid last measured voltage."""

        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute(
                "insert into measurements values (?,?,?)",
                (epoch("2026-11-06T20:30:55+02:00"), 0.0, 50.0),
            )
        self.assertEqual(
            self.run_review()["last_real_voltage_before_loss_v"], 234.8
        )

    def test_no_valid_measurement_is_rejected(self):
        """Do not invent volts when no real sample predates a gap."""

        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute("delete from measurements")
        with self.assertRaisesRegex(ValueError, "sample"):
            self.run_review()

    def test_closed_gap_does_not_claim_power_restored(self):
        """A restored Shelly connection does not verify restored mains."""

        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute(
                "update outages set ended_at=? where id=15",
                (epoch("2026-11-06T21:02:00+02:00"),),
            )
        result = self.run_review()
        self.assertIsNotNone(result["connection_restored_kyiv"])
        self.assertTrue(result["power_off_reported_by_operator"])
        self.assertIsNone(result["exact_power_off_time"])

    def test_no_forged_operator_report_on_other_incident(self):
        """An incident number requires its own matching SQLite record."""

        self.record["incident"] = "natural incident #14"
        self.write_record()
        with self.assertRaisesRegex(ValueError, "absent"):
            REVIEW.query_incident(self.db, 14)

    def test_missing_report_file_is_rejected(self):
        """The tool must not create a missing evidence file."""

        self.file.unlink()
        with self.assertRaisesRegex(ValueError, "missing"):
            REVIEW.read_incident(self.file)
        self.assertFalse(self.file.exists())

    def test_symlink_report_file_rejected(self):
        """Avoid following user-supplied private evidence symlinks."""

        real = self.file.with_name("real.json")
        self.file.rename(real)
        self.file.symlink_to(real)
        with self.assertRaises(ValueError):
            REVIEW.read_incident(self.file)

    def test_russian_ukrainian_english_messages(self):
        """No locale should imply a precise mains switching timestamp."""

        report = self.run_review()
        for language, phrase in (
            ("ru", "неизвестно"),
            ("uk", "невідомий"),
            ("en", "unknown"),
        ):
            with self.subTest(language=language):
                message = REVIEW.operator_text(report, language)
                self.assertIn(phrase, message)
                self.assertIn("20:31", message)

    def test_cli_json_does_not_change_database(self):
        """A one-shot review never edits or appends production history."""

        before = self.db.read_bytes()
        output = io.StringIO()
        args = [
            "incident-evidence-review",
            "--incident",
            str(self.file),
            "--db",
            str(self.db),
            "--json",
        ]
        with patch.object(sys, "argv", args), redirect_stdout(output):
            self.assertEqual(REVIEW.main(), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "pass")
        self.assertIsNone(result["exact_power_off_time"])
        self.assertEqual(before, self.db.read_bytes())

    def test_help_supported(self):
        """A standalone administrative tool must explain all arguments."""

        output = io.StringIO()
        with (
            patch.object(sys, "argv", ["incident-review", "--help"]),
            redirect_stdout(output),
            self.assertRaises(SystemExit) as done,
        ):
            REVIEW.parse_args()
        self.assertEqual(done.exception.code, 0)
        self.assertIn("--incident", output.getvalue())


if __name__ == "__main__":
    unittest.main()
