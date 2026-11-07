"""Prove 15-minute Shelly stability using same-phase voltage samples."""

from __future__ import annotations

import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from test_recovery_acceptance import (
    RECOVERY,
    args,
    complete_stable_rotation,
    create_database,
    make_stable_recovery,
)


class StableSampleContinuityTests(unittest.TestCase):
    """Reject silent sample gaps hidden by successful Telegram rotation."""

    def setUp(self):
        """Create a private SQLite fixture with timer and local endpoints."""

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        self.db_path = directory / "stable.sqlite3"
        self.baseline_path = directory / "baseline.json"
        create_database(self.db_path, open_outage=False)
        make_stable_recovery(self.db_path)
        self.add_samples([1000], "L1")
        responses = [
            (200, {"connectivity": "online"}),
            (200, {"ready": True}),
        ]
        with patch.object(RECOVERY, "fetch_json", side_effect=responses):
            baseline = RECOVERY.stable_baseline_payload(args(self.db_path))
        self.assertEqual(baseline["status"], "pass")
        self.baseline_path.write_text(RECOVERY.json.dumps(baseline))
        self.add_samples(range(1010, 1901, 10), "L1")
        complete_stable_rotation(self.db_path)

    def add_samples(self, stamps, phase, frequency=50.0):
        """Insert synthetic voltage in the isolated test database only."""

        with closing(RECOVERY.sqlite3.connect(self.db_path)) as db, db:
            db.executemany(
                "insert into measurements values (?,?,?,?,?)",
                [(ts, phase, 230.0, frequency, 1) for ts in stamps],
            )

    def execute(self, sql, values=()):
        """Apply a synthetic fault only to the disposable SQLite file."""

        with closing(RECOVERY.sqlite3.connect(self.db_path)) as db, db:
            db.execute(sql, values)

    def verify(self):
        """Simulate ready endpoints without sending any Telegram messages."""

        request = args(self.db_path)
        request.baseline = self.baseline_path
        responses = [
            (200, {"connectivity": "online"}),
            (200, {"ready": True}),
        ]
        with (
            patch.object(RECOVERY, "fetch_json", side_effect=responses),
            patch.object(RECOVERY.time, "time", return_value=1901.0),
        ):
            return RECOVERY.stable_verify_payload(request)

    def assert_sample_failure(self):
        """A rotated status is never sufficient without voltage evidence."""

        result = self.verify()
        self.assertEqual(result["status"], "fail")
        self.assertTrue(
            any(
                "phase" in reason or "sample" in reason
                for reason in result["errors"]
            ),
            result["errors"],
        )

    def test_continuous_l1_samples_can_pass_local_checker(self):
        """A complete real-like 15-minute L1 sample series is sufficient."""

        self.assertEqual(self.verify()["status"], "pass")

    def test_only_other_phase_does_not_prove_l1_stability(self):
        """L2 sample growth cannot substitute for missing L1 readings."""

        self.execute("delete from measurements where phase='L1' and ts>1000")
        self.add_samples(range(1010, 1902, 10), "L2")
        self.assert_sample_failure()

    def test_multi_minute_missing_sample_gap_fails(self):
        """A healthy status with a 5-minute voltage blackout is not proof."""

        self.execute(
            "delete from measurements where phase='L1' "
            "and ts>=1200 and ts<=1500"
        )
        self.assert_sample_failure()

    def test_stale_last_phase_sample_fails(self):
        """Final telemetry cannot come from minutes before verification."""

        self.execute("delete from measurements where phase='L1' and ts>1800")
        self.assert_sample_failure()

    def test_invalid_frequency_does_not_prove_stability(self):
        """Unphysical frequency values are not accepted measurements."""

        self.execute("update measurements set frequency=20 where ts>=1000")
        self.assert_sample_failure()

    def test_interleaved_overvoltage_cannot_be_filtered_out(self):
        """One 900 V reading invalidates otherwise complete 15-minute data."""

        self.add_samples([1505], "L1")
        self.execute(
            "update measurements set voltage=900 where phase='L1' and ts=1505"
        )
        self.assert_sample_failure()

    def test_interleaved_invalid_frequency_cannot_be_filtered_out(self):
        """A brief 5 Hz reading must not disappear behind normal samples."""

        self.add_samples([1505], "L1", frequency=5.0)
        self.assert_sample_failure()

    def test_interleaved_missing_voltage_cannot_be_filtered_out(self):
        """Null voltage during monitored stability must fail closed."""

        self.add_samples([1505], "L1")
        self.execute(
            "update measurements set voltage=NULL where phase='L1' and ts=1505"
        )
        self.assert_sample_failure()

    def test_interleaved_nonfinite_voltage_cannot_be_filtered_out(self):
        """An infinite voltage sample must fail the physical-range gate."""

        self.add_samples([1505], "L1")
        self.execute(
            "update measurements set voltage=? where phase='L1' and ts=1505",
            (float("inf"),),
        )
        self.assert_sample_failure()

    def test_critical_low_voltage_during_stability_fails(self):
        """185 V is not normal at configured 207 V warning threshold."""

        self.execute(
            "UPDATE measurements SET voltage=185 WHERE phase='L1' AND ts=1500"
        )
        self.assert_sample_failure()

    def test_warning_high_voltage_during_stability_fails(self):
        """260 V is not stable recovery under configured 253 V upper band."""

        self.execute(
            "UPDATE measurements SET voltage=260 WHERE phase='L1' AND ts=1500"
        )
        self.assert_sample_failure()

    def test_exact_warning_boundaries_are_not_normal(self):
        """The stored warning thresholds are inclusive warning bands."""

        for voltage in (207.0, 253.0):
            with self.subTest(voltage=voltage):
                self.execute(
                    "UPDATE measurements SET voltage=? "
                    "WHERE phase='L1' AND ts=1500",
                    (voltage,),
                )
                self.assert_sample_failure()

    def test_subwarning_observation_does_not_change_healthy_samples(self):
        """Ordinary values remain valid even with strict range checking."""

        for voltage in (207.1, 252.9):
            with self.subTest(voltage=voltage):
                self.execute(
                    "UPDATE measurements SET voltage=? "
                    "WHERE phase='L1' AND ts=1500",
                    (voltage,),
                )
                self.assertEqual(self.verify()["status"], "pass")

    def test_changed_operator_thresholds_invalidate_baseline(self):
        """Do not silently move the normal window after capturing it."""

        self.execute(
            "UPDATE meta SET value='185' "
            "WHERE key='telegram_setting_warning_low'"
        )
        verdict = self.verify()
        self.assertEqual(verdict["status"], "fail")
        self.assertIn(
            "normal voltage thresholds changed during recovery",
            verdict["errors"],
        )

    def test_missing_thresholds_cannot_give_false_pass(self):
        """Fail closed if the deployment's actual boundaries are unknown."""

        self.execute(
            "DELETE FROM meta WHERE key='telegram_setting_warning_high'"
        )
        verdict = self.verify()
        self.assertEqual(verdict["status"], "fail")
        self.assertIn(
            "normal voltage thresholds unavailable", verdict["errors"]
        )

    def test_custom_normal_band_is_honored(self):
        """Narrow administrator-configured boundaries supersede defaults."""

        self.execute(
            "UPDATE meta SET value='225' "
            "WHERE key='telegram_setting_warning_low'"
        )
        self.execute(
            "UPDATE meta SET value='240' "
            "WHERE key='telegram_setting_warning_high'"
        )
        with closing(RECOVERY.sqlite3.connect(self.db_path)) as db, db:
            db.execute("UPDATE measurements SET voltage=230 WHERE phase='L1'")
        verdict = self.verify()
        self.assertEqual(verdict["status"], "fail")

    def test_future_dated_sample_does_not_fill_missing_interval(self):
        """Future timestamps must not make a missing final minute fresh."""

        self.execute("delete from measurements where phase='L1' and ts>1800")
        self.add_samples([1950], "L1")
        self.assert_sample_failure()

    def test_missing_first_minute_fails(self):
        """Evidence must begin near the original stable-recovery event."""

        self.execute(
            "delete from measurements where phase='L1' "
            "and ts>=1000 and ts<=1100"
        )
        self.assert_sample_failure()

    def test_verifier_does_not_modify_db_bytes(self):
        """Stable check is strictly read-only on a temporary database."""

        before = self.db_path.read_bytes()
        self.assertEqual(self.verify()["status"], "pass")
        self.assertEqual(before, self.db_path.read_bytes())


if __name__ == "__main__":
    unittest.main()
