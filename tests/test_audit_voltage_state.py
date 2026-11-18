"""Exercise audit regressions through the monitor and real temporary SQLite."""

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

from test_app import APP, config


class AuditVoltageStateTests(unittest.TestCase):
    """Protect voltage transitions, restart, and settings against data loss."""

    def setUp(self):
        """Use a private database and stub external Telegram calls."""

        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "voltage.sqlite3"
        self.now = 1_794_960_000
        self.bot = self.make_monitor()

    def make_monitor(self, **overrides):
        """Initialize in the same order as the real service."""

        values = dict(
            database_path=self.path,
            telegram_token="synthetic",
            telegram_chat_id="123",
            telegram_status_chat_id="-100999",
            alerts_enabled=True,
            low_voltage_alert_enabled=False,
        )
        values.update(overrides)
        bot = APP.Monitor(config(**values))
        bot.telegram.send = Mock(return_value=101)
        for method in ("edit", "set_commands", "clear_reply_markup"):
            setattr(bot.telegram, method, Mock())
        bot.telegram.quiet_now = Mock(return_value=False)
        with patch.object(APP, "start_health_server"):
            bot.initialize()
        return bot

    def poll(self, volts, phase="L1"):
        """Take one real monitor step using a synthetic RPC sample."""

        self.now += 20
        with (
            patch.object(APP.time, "time", return_value=self.now),
            patch.object(APP.time, "monotonic", return_value=self.now),
        ):
            self.bot.accept_phase_sample(phase, (volts, 50, 1), self.now)

    def test_critical_direction_changes_reuse_the_status(self):
        """Different directions within the window update the same post."""

        for volts in (189, 260, 189):
            self.poll(volts)
            self.poll(volts)
        self.poll(230)
        self.poll(230)
        self.poll(230)
        self.assertEqual(self.bot.last_voltage_event[0], "recovered")
        self.bot.telegram.send.assert_called_once()
        edits = self.bot.telegram.edit.call_args_list
        self.assertEqual([call.args[0] for call in edits], [101, 101, 101])
        self.assertIn("Критически повышенное", edits[0].args[1])
        self.assertIn("Критически пониженное", edits[1].args[1])

    def test_secondary_warning_direction_is_independent(self):
        """L2 retains its confirmed side when L1 also has online readings."""

        self.bot = self.make_monitor(shelly_meter_type="pro3em")
        for volts in (205, 254):
            self.poll(volts, "L2")
            self.poll(volts, "L2")
            self.poll(230)
        event = self.bot.last_voltage_event
        self.assertEqual((event[0], event[4]), ("warning_high", "L2"))

    def test_restart_after_alert_confirms_recovery(self):
        """Restart does not resend an unchanged alert or skip normal dwell."""

        self.poll(189)
        self.poll(189)
        self.bot = self.make_monitor()
        self.poll(189)
        self.poll(189)
        self.bot.telegram.send.assert_not_called()
        self.poll(230)
        self.poll(230)
        self.assertEqual(self.bot.last_voltage_event[0], "critical_low")
        self.poll(230)
        self.assertEqual(self.bot.last_voltage_event[0], "recovered")
        self.bot.telegram.edit.assert_called_once()
        self.assertEqual(self.bot.telegram.edit.call_args.args[0], 101)

    def test_restart_reads_legacy_event_without_detector_state(self):
        """Upgrade old metadata without a schema change."""

        self.bot.storage.set_meta(
            "last_voltage_event",
            json.dumps(
                dict(
                    kind="critical_high",
                    voltage=260,
                    frequency=50,
                    timestamp=self.now,
                    phase="L1",
                )
            ),
        )
        self.bot = self.make_monitor()
        for _ in range(3):
            self.poll(230)
        self.assertEqual(self.bot.last_voltage_event[0], "recovered")

    def test_stale_reading_is_hidden_in_text_and_metrics(self):
        """A green reading does not survive past the configured stale limit."""

        self.poll(230)
        with patch.object(APP.time, "time", return_value=self.now + 100):
            self.assertEqual(APP.readiness(self.bot)[0], 503)
            text = APP.multiphase_status_text(self.bot)
            self.assertIn("Показания Shelly устарели", text)
            self.assertNotIn("🟢 L: 230", text)
            self.assertNotIn("Частота:", text)
            self.assertIn(
                "shelly_voltage_volts NaN", APP.metrics_text(self.bot)
            )

    def test_offline_daily_stats_use_today_after_midnight(self):
        """Previous-day stats cannot appear as current-day values."""

        stamp = APP.datetime(
            2026, 11, 18, 23, 59, tzinfo=APP.ZoneInfo("Europe/Kyiv")
        ).timestamp()
        self.bot.storage.add(stamp, 179.4, 50)
        self.bot.state.restore(stamp, 179.4, 50)
        self.bot.state.error("offline")
        with patch.object(APP.time, "time", return_value=stamp + 120):
            text = APP.multiphase_status_text(self.bot)
        self.assertIn("L сегодня: —–—", text)
        self.assertNotIn("179.4", text)

    def test_linked_settings_roll_back_with_audit_on_failure(self):
        """SQLite rejects one write without leaving a half applied setting."""

        with closing(self.bot.storage.connect()) as db, db:
            db.execute(
                "CREATE TRIGGER reject_recovery BEFORE INSERT ON meta "
                "WHEN NEW.key='telegram_setting_recovery_v' "
                "BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END;"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.bot.update_setting("low_inc", "7")
        self.assertEqual(float(self.bot.setting_value("low_v")), 180)
        self.assertEqual(float(self.bot.setting_value("recovery_v")), 185)
        self.assertEqual(self.bot.storage.audit_recent(), [])
        self.assertEqual(self.bot.low_voltage_alert_v, 180)
        with closing(self.bot.storage.connect()) as db, db:
            db.execute("DROP TRIGGER reject_recovery")
        self.bot.update_setting("low_inc", "7")
        self.assertEqual(len(self.bot.storage.audit_recent()), 2)
        self.assertEqual(self.bot.low_voltage_alert_v, 185)
        self.assertEqual(self.bot.low_voltage_recovery_v, 190)

    def test_failed_recovery_does_not_count_a_new_outage(self):
        """A short recovery attempt preserves the open incident count."""

        with patch.object(self.bot.shelly, "read", side_effect=ValueError):
            for _ in range(3):
                self.bot.poll_cycle()
            self.poll(230)
            self.bot.poll_cycle()
        self.assertEqual(self.bot.state.snapshot()["outage_count"], 1)
        self.assertEqual(self.bot.storage.outage_total(), 1)

    def test_nonfinite_runtime_config_is_rejected(self):
        """Refuse non-numeric timing values before starting polling."""

        for field in ("poll_interval", "shelly_timeout", "alert_dwell_sec"):
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(ValueError, "must be finite"),
            ):
                APP.validate_config(config(**{field: float("nan")}))
