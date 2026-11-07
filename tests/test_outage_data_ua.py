"""Networkless tests for community outage forecasts."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import outage_data_ua as ADAPTER

KYIV = ZoneInfo("Europe/Kyiv")
NOW = datetime(2026, 11, 8, 1, tzinfo=KYIV)


def fake_snapshot(updates: dict | None = None) -> dict:
    """Build a minimal published-state document for the test group."""

    hours = {str(i): "yes" for i in range(1, 25)}
    hours.update(updates or {})
    day = str(int(datetime(2026, 11, 8, tzinfo=KYIV).timestamp()))
    return {
        "regionId": "kyiv",
        "lastUpdated": (NOW - timedelta(minutes=5)).isoformat(),
        "lastUpdateStatus": {"ok": True, "status": "parsed", "code": 200},
        "meta": {"schemaVersion": "1.0.0"},
        "fact": {"data": {day: {"GPV10.1": hours}}},
    }


class CommunityOutageTests(unittest.TestCase):
    """Keep false outage information out of the monitoring bot."""

    def test_half_hours_are_exact(self):
        """Certain and possible outages are distinct windows."""

        data = fake_snapshot(
            {
                "1": "no",
                "2": "first",
                "3": "second",
                "4": "maybe",
                "5": "mfirst",
                "6": "msecond",
            }
        )
        result = ADAPTER.parse_snapshot(data, "kyiv", "10.1", NOW)
        self.assertEqual(
            result.definite, (("00:00", "01:30"), ("02:30", "03:00"))
        )
        self.assertEqual(
            result.possible, (("03:00", "04:30"), ("05:30", "06:00"))
        )
        self.assertIn("unofficial mirror", ADAPTER.format_forecast(result))
        self.assertIn("Actual mains", ADAPTER.format_forecast(result))

    def test_wrong_region_fails(self):
        """A regional mix-up must not silently choose a different feed."""

        with self.assertRaisesRegex(ValueError, "region mismatch"):
            ADAPTER.parse_snapshot(fake_snapshot(), "kyiv-region", "10.1", NOW)

    def test_missing_group_fails(self):
        """Do not silently substitute another electrical outage group."""

        with self.assertRaisesRegex(ValueError, "group"):
            ADAPTER.parse_snapshot(fake_snapshot(), "kyiv", "10.2", NOW)

    def test_parser_error_fails(self):
        """Upstream errors do not refresh retained schedules."""

        data = fake_snapshot()
        data["lastUpdateStatus"]["ok"] = False
        with self.assertRaisesRegex(ValueError, "parser"):
            ADAPTER.parse_snapshot(data, "kyiv", "10.1", NOW)

    def test_stale_and_future_snapshots_fail(self):
        """Reject clock skew and older cached information."""

        for elapsed in (timedelta(hours=3), -timedelta(minutes=3)):
            with self.subTest(elapsed=elapsed):
                data = fake_snapshot()
                data["lastUpdated"] = (NOW - elapsed).isoformat()
                with self.assertRaisesRegex(ValueError, "stale"):
                    ADAPTER.parse_snapshot(data, "kyiv", "10.1", NOW)

    def test_wrong_day_fails(self):
        """Reject yesterday's document even when it has fresh metadata."""

        data = fake_snapshot()
        day = str(int(datetime(2026, 11, 7, tzinfo=KYIV).timestamp()))
        groups = next(iter(data["fact"]["data"].values()))
        data["fact"]["data"] = {day: groups}
        with self.assertRaisesRegex(ValueError, "today"):
            ADAPTER.parse_snapshot(data, "kyiv", "10.1", NOW)

    def test_incomplete_or_unknown_hour_fails(self):
        """Unknown codes cannot be guessed into outage hours."""

        data = fake_snapshot({"24": "unknown"})
        with self.assertRaisesRegex(ValueError, "unknown"):
            ADAPTER.parse_snapshot(data, "kyiv", "10.1", NOW)
        data = fake_snapshot()
        hours = next(iter(data["fact"]["data"].values()))["GPV10.1"]
        del hours["24"]
        with self.assertRaisesRegex(ValueError, "incomplete"):
            ADAPTER.parse_snapshot(data, "kyiv", "10.1", NOW)

    def test_symlinks_and_oversized_input_fail(self):
        """Prevent traversals and huge untrusted documents."""

        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "snapshot.json"
            file.write_text("X" * (ADAPTER.MAX_BYTES + 1))
            with self.assertRaisesRegex(ValueError, "too large"):
                ADAPTER.read_snapshot(file)
            shortcut = Path(folder) / "alias.json"
            shortcut.symlink_to(file)
            with self.assertRaisesRegex(ValueError, "regular"):
                ADAPTER.read_snapshot(shortcut)
            file.write_text(json.dumps(fake_snapshot()))
            self.assertIsInstance(ADAPTER.read_snapshot(file), dict)

    def test_full_day_and_empty_windows(self):
        """Preserve midnight boundaries and do not assert power is present."""

        data = fake_snapshot({str(i): "no" for i in range(1, 25)})
        result = ADAPTER.parse_snapshot(data, "kyiv", "10.1", NOW)
        self.assertEqual(result.definite, (("00:00", "24:00"),))
        normal = ADAPTER.parse_snapshot(fake_snapshot(), "kyiv", "10.1", NOW)
        self.assertEqual(normal.definite, ())
        self.assertEqual(normal.possible, ())

    def test_schema_changes_fail_closed(self):
        """Do not trust data that changes schema without a review."""

        data = fake_snapshot()
        data["meta"]["schemaVersion"] = "2.0.0"
        with self.assertRaisesRegex(ValueError, "schema"):
            ADAPTER.parse_snapshot(data, "kyiv", "10.1", NOW)


if __name__ == "__main__":
    unittest.main()
