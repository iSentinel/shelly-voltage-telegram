"""Full recovery verification must require fresh same-phase readings."""

from __future__ import annotations

import tempfile
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from test_recovery_acceptance import RECOVERY, args

START = "2026-11-06T20:31:07+02:00"
END = "2026-11-07T00:40:00+02:00"
NOW = "2026-11-07T00:40:08+02:00"


def timestamp(raw: str) -> float:
    """Use explicit Kyiv offsets for real-like event time boundaries."""

    return datetime.fromisoformat(raw).timestamp()


class RecoveryAcceptanceFreshnessTests(unittest.TestCase):
    """Reject unrelated or stale rows even with green health endpoints."""

    def setUp(self):
        """Build an isolated incident 15 baseline with one old L1 sample."""

        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        folder = Path(workspace.name)
        self.path = folder / "fake.sqlite3"
        self.checkpoint = folder / "baseline.json"
        with closing(RECOVERY.sqlite3.connect(self.path)) as db, db:
            db.executescript(
                "create table measurements (ts real,phase text,"
                "voltage real,frequency real,rpc_ms real);"
                "create table outages (id integer primary key,"
                "started_at real,ended_at real,phase text);"
                "create table meta (key text primary key,value text);"
            )
            db.execute(
                "insert into measurements values (?,?,?,?,?)",
                (
                    timestamp("2026-11-06T20:30:25+02:00"),
                    "L1",
                    234.8,
                    50.0,
                    1.0,
                ),
            )
            db.execute(
                "insert into outages values (?,?,?,?)",
                (15, timestamp(START), None, "L1"),
            )
            db.execute(
                "insert into meta values (?,?)",
                ("telegram_status_message_id", "682"),
            )
        baseline = RECOVERY.baseline_payload(args(self.path))
        self.assertEqual(baseline["status"], "pass")
        self.checkpoint.write_text(RECOVERY.json.dumps(baseline))
        self.at = timestamp(NOW)

    def close_and_rotate(self):
        """Simulate the network recovery and Telegram status ID change."""

        with closing(RECOVERY.sqlite3.connect(self.path)) as db, db:
            db.execute(
                "update outages set ended_at=? where id=15",
                (timestamp(END),),
            )
            db.execute(
                "update meta set value=? where key=?",
                ("700", "telegram_status_message_id"),
            )

    def insert_sample(self, phase: str, at: str, frequency=50.0):
        """Insert a measurement into only the temporary SQLite database."""

        with closing(RECOVERY.sqlite3.connect(self.path)) as db, db:
            db.execute(
                "insert into measurements values (?,?,?,?,?)",
                (timestamp(at), phase, 230.0, frequency, 1.0),
            )

    def verify(self):
        """Pass synthetic online/ready responses; require SQLite evidence."""

        request = args(self.path)
        request.baseline = self.checkpoint
        responses = [
            (200, {"connectivity": "online"}),
            (200, {"ready": True}),
        ]
        with (
            patch.object(RECOVERY, "fetch_json", side_effect=responses),
            patch.object(RECOVERY.time, "time", return_value=self.at),
        ):
            return RECOVERY.verify_payload(request)

    def test_open_connection_loss_never_passes(self):
        """No schedule boundary can close incident 15."""

        self.insert_sample("L1", "2026-11-07T00:40:03+02:00")
        result = self.verify()
        self.assertEqual(result["status"], "fail")
        self.assertIn("captured outage is still open", result["errors"])

    def test_other_phase_must_not_prove_l1_recovery(self):
        """New L2 data cannot satisfy the L1 event under verification."""

        self.close_and_rotate()
        self.insert_sample("L2", "2026-11-07T00:40:03+02:00")
        result = self.verify()
        self.assertEqual(result["status"], "fail")
        self.assertTrue(any("fresh" in text for text in result["errors"]))

    def test_old_l1_reading_must_not_prove_recovery(self):
        """An old 234.8 V sample cannot be presented as current supply."""

        self.close_and_rotate()
        self.insert_sample("L1", "2026-11-07T00:35:00+02:00")
        result = self.verify()
        self.assertEqual(result["status"], "fail")
        self.assertTrue(any("fresh" in text for text in result["errors"]))

    def test_l1_sample_too_far_before_gap_end_is_rejected(self):
        """A network-up timestamp must be supported by close readings."""

        self.close_and_rotate()
        self.insert_sample("L1", "2026-11-07T00:39:40+02:00")
        result = self.verify()
        self.assertEqual(result["status"], "fail")
        self.assertTrue(any("near" in text for text in result["errors"]))

    def test_future_l1_timestamp_is_rejected(self):
        """Clock skew must not make a future reading look current."""

        self.close_and_rotate()
        self.insert_sample("L1", "2026-11-07T00:40:20+02:00")
        result = self.verify()
        self.assertEqual(result["status"], "fail")
        self.assertTrue(any("fresh" in text for text in result["errors"]))

    def test_invalid_frequency_is_not_valid_evidence(self):
        """The verifier must ignore structurally invalid meter samples."""

        self.close_and_rotate()
        self.insert_sample("L1", "2026-11-07T00:40:03+02:00", frequency=20.0)
        result = self.verify()
        self.assertEqual(result["status"], "fail")
        self.assertTrue(any("fresh" in text for text in result["errors"]))

    def test_newer_invalid_frequency_never_uses_previous_good_sample(self):
        """Fresh good L1 cannot hide the latest impossible L1 frequency."""

        self.close_and_rotate()
        self.insert_sample("L1", "2026-11-07T00:40:03+02:00")
        self.assertEqual(self.verify()["status"], "pass")
        self.insert_sample("L1", "2026-11-07T00:40:06+02:00", frequency=5.0)
        result = self.verify()
        self.assertEqual(result["status"], "fail")
        self.assertTrue(any("fresh" in e for e in result["errors"]))

    def test_newer_overvoltage_never_uses_previous_good_sample(self):
        """A 900 V latest sample makes the recovery unverified."""

        self.close_and_rotate()
        self.insert_sample("L1", "2026-11-07T00:40:03+02:00")
        self.insert_sample("L1", "2026-11-07T00:40:06+02:00")
        with closing(RECOVERY.sqlite3.connect(self.path)) as db, db:
            db.execute(
                "update measurements set voltage=900 "
                "where phase='L1' and ts=?",
                (timestamp("2026-11-07T00:40:06+02:00"),),
            )
        result = self.verify()
        self.assertEqual(result["status"], "fail")
        self.assertTrue(any("fresh" in e for e in result["errors"]))

    def test_newer_missing_voltage_never_uses_previous_good_sample(self):
        """A null latest meter voltage cannot be treated as recovery."""

        self.close_and_rotate()
        self.insert_sample("L1", "2026-11-07T00:40:03+02:00")
        self.insert_sample("L1", "2026-11-07T00:40:06+02:00")
        with closing(RECOVERY.sqlite3.connect(self.path)) as db, db:
            db.execute(
                "update measurements set voltage=NULL "
                "where phase='L1' and ts=?",
                (timestamp("2026-11-07T00:40:06+02:00"),),
            )
        self.assertEqual(self.verify()["status"], "fail")

    def test_other_phase_invalid_latest_does_not_hide_valid_l1(self):
        """The captured phase, not unrelated L2 data, determines recovery."""

        self.close_and_rotate()
        self.insert_sample("L1", "2026-11-07T00:40:03+02:00")
        self.insert_sample("L2", "2026-11-07T00:40:06+02:00", frequency=5.0)
        self.assertEqual(self.verify()["status"], "pass")

    def test_genuine_recent_same_phase_can_pass_local_gates(self):
        """A fresh L1 reading makes a candidate, not subscriber proof."""

        self.close_and_rotate()
        self.insert_sample("L1", "2026-11-07T00:40:03+02:00")
        result = self.verify()
        self.assertEqual(result["status"], "pass")
        self.assertNotIn("status_message_id", RECOVERY.json.dumps(result))


if __name__ == "__main__":
    unittest.main()
