"""Offline tests for stable fifteen-minute Shelly recovery evidence."""

from __future__ import annotations

import contextlib
import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "em50_stability", ROOT / "scripts/em50-recovery-stability.py"
)
assert SPEC is not None and SPEC.loader is not None
STABLE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STABLE)


def samples(now: float, length: int = 90) -> list[tuple]:
    """Return ten-second interval synthetic physical samples."""

    return [(now - 900 + 5 + i * 10, 213.0, 50.0) for i in range(length)]


def state(now: float, age: int = 1200) -> dict:
    """Stand in for a natural closed incident before the stable window."""

    return {"end": now - age, "phase": "L1", "outage_id": 18}


def args(path: str) -> SimpleNamespace:
    """Use private synthetic file paths and the default acceptance limits."""

    return SimpleNamespace(
        db=path,
        health_url="http://127.0.0.1:8080/health",
        ready_url="http://127.0.0.1:8080/ready",
        max_gap_seconds=30,
        min_samples=60,
    )


class RecoveryStabilityTests(unittest.TestCase):
    """Do not claim continuity from sparse or fabricated samples."""

    def setUp(self):
        """Create one fake current time and sample series."""

        self.now = 2000000000.0
        self.rows = samples(self.now)

    def test_ninety_real_samples_pass_without_telegram_proof(self):
        """Successful physical recovery still requires human approval."""

        result = STABLE.verify_stability(
            self.rows, self.now - 900, self.now, 30, 60
        )
        self.assertEqual(result["window_seconds"], 900)
        self.assertEqual(result["sample_count"], 90)
        self.assertLess(result["largest_gap_seconds"], 30)

    def test_too_few_samples_fail(self):
        """A few successful polls cannot prove 15-minute stability."""

        with self.assertRaisesRegex(ValueError, "too few"):
            STABLE.verify_stability(
                self.rows[:15], self.now - 900, self.now, 30, 60
            )

    def test_gap_in_middle_fails_even_with_enough_samples(self):
        """A sustained polling pause invalidates continuous recovery."""

        broken = self.rows[:30] + self.rows[36:]
        with self.assertRaisesRegex(ValueError, "gap"):
            STABLE.verify_stability(broken, self.now - 900, self.now, 30, 60)

    def test_gap_at_window_start_and_end_fails(self):
        """No hidden 15-minute coverage gaps are permitted."""

        for broken in (self.rows[6:], self.rows[:-6]):
            with (
                self.subTest(count=len(broken)),
                self.assertRaisesRegex(ValueError, "gap"),
            ):
                STABLE.verify_stability(
                    broken, self.now - 900, self.now, 30, 60
                )

    def test_false_voltage_and_frequency_rejected(self):
        """Reject impossible, fabricated and nonnumeric values."""

        for row in (
            (self.rows[12][0], 0, 50.0),
            (self.rows[12][0], 213.0, 0),
            (self.rows[12][0], True, 50.0),
            (self.rows[12][0], 213.0, float("nan")),
        ):
            broken = self.rows.copy()
            broken[12] = row
            with (
                self.subTest(metric=row),
                self.assertRaisesRegex(ValueError, "invalid physical"),
            ):
                STABLE.verify_stability(
                    broken, self.now - 900, self.now, 30, 60
                )

    def test_thresholds_cannot_be_weakened_without_validation(self):
        """Unreasonable acceptance parameters must not pass."""

        for max_gap, minimum in ((300, 60), (30, 1), (0, 60)):
            with (
                self.subTest(max_gap=max_gap, minimum=minimum),
                self.assertRaisesRegex(ValueError, "thresholds"),
            ):
                STABLE.verify_stability(
                    self.rows, self.now - 900, self.now, max_gap, minimum
                )

    def test_recent_recovery_before_full_fifteen_minutes_fails(self):
        """An incident ended 14 minutes ago cannot pass the full window."""

        with (
            patch.object(
                STABLE.NATURAL,
                "read_state",
                return_value=state(self.now, age=840),
            ),
            patch.object(
                STABLE.NATURAL,
                "local_json",
                side_effect=[{"connectivity": "online"}, {"ready": True}],
            ),
            patch.object(STABLE.NATURAL, "check_recovery"),
            patch.object(STABLE, "read_window") as read_window,
            self.assertRaisesRegex(ValueError, "not elapsed"),
        ):
            STABLE.inspect(args("/fake"), self.now)
        read_window.assert_not_called()

    def test_inspect_still_never_authorizes_or_claims_channel_receipt(self):
        """A green automated check must not become release acceptance."""

        with (
            patch.object(
                STABLE.NATURAL, "read_state", return_value=state(self.now)
            ),
            patch.object(
                STABLE.NATURAL,
                "local_json",
                side_effect=[{"connectivity": "online"}, {"ready": True}],
            ),
            patch.object(STABLE.NATURAL, "check_recovery"),
            patch.object(STABLE, "read_window", return_value=self.rows),
        ):
            report = STABLE.inspect(args("/fake"), self.now)
        self.assertEqual(report["status"], "PASS")
        self.assertFalse(report["release_authorized"])
        self.assertEqual(
            report["telegram_channel_edit_visible"], "NOT_VERIFIED"
        )
        self.assertNotIn("chat_id", json.dumps(report))

    def test_read_window_opens_existing_sqlite_read_only(self):
        """The monitoring database remains byte-for-byte unchanged."""

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fixture.sqlite3"
            with contextlib.closing(sqlite3.connect(path)) as db:
                db.execute(
                    "CREATE TABLE measurements "
                    "(ts REAL,voltage REAL,frequency REAL,phase TEXT)"
                )
                db.executemany(
                    "INSERT INTO measurements VALUES (?,?,?,?)",
                    [(t, v, f, "L1") for t, v, f in self.rows],
                )
                db.commit()
            original = path.read_bytes()
            observed = STABLE.read_window(path, "L1", self.now - 900, self.now)
            self.assertEqual(len(observed), 90)
            self.assertEqual(path.read_bytes(), original)

    def test_missing_database_is_not_created(self):
        """Never implicitly create a production-like database."""

        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing.sqlite3"
            with self.assertRaisesRegex(ValueError, "missing"):
                STABLE.read_window(missing, "L1", self.now - 900, self.now)
            self.assertFalse(missing.exists())

    def test_no_client_receipt_or_telegram_calls_in_source(self):
        """Stability is exclusively a local read-only data check."""

        text = (ROOT / "scripts/em50-recovery-stability.py").read_text(
            encoding="utf-8"
        )
        for forbidden in (
            "sendMessage",
            "editMessageText",
            "deleteMessage",
            "pinChatMessage",
            "telegram_token",
        ):
            with self.subTest(method=forbidden):
                self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
