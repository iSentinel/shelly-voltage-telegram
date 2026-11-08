"""Offline acceptance of source, time and slot integrity for YASNO."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import yasno_schedule as Y

KYIV = ZoneInfo("Europe/Kyiv")
NOW = datetime(2026, 11, 8, 2, tzinfo=KYIV)
FETCHED = NOW - timedelta(seconds=30)


def response(now: datetime = NOW) -> dict:
    """Use the exact five-interval schema observed on Kyiv group 10.1."""

    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day = midnight.isoformat()
    published = (now - timedelta(hours=1)).isoformat()
    slots = [
        {"start": 0, "end": 240, "type": "NotPlanned"},
        {"start": 240, "end": 690, "type": "Definite"},
        {"start": 690, "end": 870, "type": "NotPlanned"},
        {"start": 870, "end": 1320, "type": "Definite"},
        {"start": 1320, "end": 1440, "type": "NotPlanned"},
    ]
    return {
        "10.1": {
            "today": {
                "date": day,
                "status": "ScheduleApplies",
                "slots": slots,
            },
            "tomorrow": {
                "date": (midnight + timedelta(days=1)).isoformat(),
                "status": "WaitingForSchedule",
                "slots": [],
            },
            "updatedOn": published,
        }
    }


class YasnoAdapterTests(unittest.TestCase):
    """Never confuse a published forecast with actual power measurements."""

    def test_real_five_slot_schedule_matches_kyiv(self):
        """Produce two definite windows and 48 bounded half-hours."""

        result = Y.parse_schedule(response(), "10.1", NOW, FETCHED)
        self.assertEqual(result.source, "yasno-kyiv-dtek")
        self.assertEqual(len(result.bars), 48)
        self.assertEqual(
            result.bars, "O" * 8 + "X" * 15 + "O" * 6 + "X" * 15 + "O" * 4
        )
        self.assertEqual(
            Y.outage_windows(result.bars), ["04:00–11:30", "14:30–22:00"]
        )

    def test_waiting_for_schedule_is_not_no_outages(self):
        """An unpublished day must not become a green timeline."""

        data = response()
        data["10.1"]["today"]["status"] = "WaitingForSchedule"
        with self.assertRaisesRegex(ValueError, "not published"):
            Y.parse_schedule(data, "10.1", NOW, FETCHED)

    def test_emergency_and_unknown_statuses_rejected(self):
        """Emergency and schema-change statuses cannot imply a plan."""

        for status in ("EmergencyShutdowns", "", "ScheduleUnknown"):
            with self.subTest(status=status):
                data = response()
                data["10.1"]["today"]["status"] = status
                with self.assertRaises(ValueError):
                    Y.parse_schedule(data, "10.1", NOW, FETCHED)

    def test_outdated_or_future_capture_is_rejected(self):
        """Distinguish successful retrieval from old local cache files."""

        for date in (
            NOW - timedelta(seconds=7201),
            NOW + timedelta(seconds=1),
        ):
            with (
                self.subTest(date=date),
                self.assertRaisesRegex(ValueError, "capture"),
            ):
                Y.parse_schedule(response(), "10.1", NOW, date)

    def test_old_published_time_is_rejected(self):
        """A successful fetch does not make an old schedule fresh."""

        data = response()
        data["10.1"]["updatedOn"] = (NOW - timedelta(hours=37)).isoformat()
        with self.assertRaisesRegex(ValueError, "publication"):
            Y.parse_schedule(data, "10.1", NOW, FETCHED)

    def test_wrong_calendar_day_or_missing_offset(self):
        """Never present yesterday or naive time as today's plan."""

        for day in (
            "2026-11-07T00:00:00+02:00",
            "2026-11-08T00:00:00",
            "2026-11-08T01:00:00+02:00",
        ):
            with self.subTest(day=day):
                data = response()
                data["10.1"]["today"]["date"] = day
                with self.assertRaises(ValueError):
                    Y.parse_schedule(data, "10.1", NOW, FETCHED)

    def test_invalid_group_and_missing_group(self):
        """Refuse injection-like group labels and absent queue IDs."""

        for group in ("GPV10.1", "<script>", "10.1/../", "99.1"):
            with (
                self.subTest(group=group),
                self.assertRaises(ValueError),
            ):
                Y.parse_schedule(response(), group, NOW, FETCHED)

    def test_overlap_gap_and_incomplete_day_are_rejected(self):
        """All 48 slots must be covered exactly once."""

        for start, end in ((241, 690), (210, 690), (240, 1450)):
            with self.subTest(start=start, end=end):
                data = response()
                data["10.1"]["today"]["slots"][1].update(start=start, end=end)
                with self.assertRaises(ValueError):
                    Y.parse_schedule(data, "10.1", NOW, FETCHED)
        data = response()
        data["10.1"]["today"]["slots"].pop()
        with self.assertRaises(ValueError):
            Y.parse_schedule(data, "10.1", NOW, FETCHED)

    def test_unknown_slot_and_boolean_minutes_are_rejected(self):
        """Do not quietly mark unexpected slot types as electricity on."""

        for kind in ("Emergency", "Probable", None):
            with self.subTest(kind=kind):
                data = response()
                data["10.1"]["today"]["slots"][1]["type"] = kind
                with self.assertRaises(ValueError):
                    Y.parse_schedule(data, "10.1", NOW, FETCHED)
        data = response()
        data["10.1"]["today"]["slots"][1]["start"] = True
        with self.assertRaises(ValueError):
            Y.parse_schedule(data, "10.1", NOW, FETCHED)

    def test_possible_slots_are_not_definite(self):
        """An explicitly possible state must stay an uncertainty marker."""

        data = response()
        data["10.1"]["today"]["slots"][1]["type"] = "Possible"
        actual = Y.parse_schedule(data, "10.1", NOW, FETCHED)
        self.assertEqual(actual.bars[8:23], "?" * 15)

    def test_kiev_dst_switches_keep_local_midnight(self):
        """Apply DST correctly for both 2026 transition days."""

        for time in (
            datetime(2026, 3, 29, 12, tzinfo=KYIV),
            datetime(2026, 10, 25, 12, tzinfo=KYIV),
        ):
            with self.subTest(day=time.date()):
                actual = Y.parse_schedule(
                    response(time), "10.1", time, time - timedelta(minutes=10)
                )
                self.assertEqual(actual.day, time.date().isoformat())

    def test_cache_size_and_symlinks_are_rejected(self):
        """Only bounded regular files can be used as offline fixtures."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            valid = root / "cache.json"
            valid.write_text(json.dumps(response()))
            data, captured = Y.read_cached(str(valid))
            self.assertIn("10.1", data)
            self.assertIsNotNone(captured.tzinfo)
            link = root / "alias.json"
            link.symlink_to(valid)
            with self.assertRaises(ValueError):
                Y.read_cached(str(link))
            valid.write_bytes(b"x" * (Y.MAX_BYTES + 1))
            with self.assertRaisesRegex(ValueError, "bounded"):
                Y.read_cached(str(valid))

    def test_fetch_pins_https_endpoint_and_limits_body(self):
        """The opt-in API fetch must reject redirects and oversized data."""

        def mock_open(response_value):
            client = Mock()
            client.__enter__ = Mock(return_value=response_value)
            client.__exit__ = Mock(return_value=False)
            return client

        raw = json.dumps(response()).encode()
        result = Mock(status=200)
        result.geturl.return_value = Y.YASNO_URL
        result.headers = {"Content-Type": "application/json"}
        result.read.return_value = raw
        with patch("urllib.request.build_opener") as opener:
            opener.return_value.open.return_value = mock_open(result)
            data, retrieved = Y.fetch_live()
        self.assertIn("10.1", data)
        self.assertIsNotNone(retrieved.tzinfo)

        result.geturl.return_value = "https://attacker.example/redirect"
        with patch("urllib.request.build_opener") as opener:
            opener.return_value.open.return_value = mock_open(result)
            with self.assertRaisesRegex(ValueError, "different endpoint"):
                Y.fetch_live()

    def test_fetch_rejects_oversize_and_html(self):
        """Refuse huge API responses and unexpected HTML challenges."""

        raw = json.dumps(response()).encode()
        reply = Mock(status=200)
        reply.geturl.return_value = Y.YASNO_URL
        reply.headers = {"Content-Type": "text/html"}
        reply.read.return_value = raw
        opener = Mock()
        opener.open.return_value.__enter__ = Mock(return_value=reply)
        opener.open.return_value.__exit__ = Mock(return_value=False)
        with (
            patch("urllib.request.build_opener", return_value=opener),
            self.assertRaisesRegex(ValueError, "JSON"),
        ):
            Y.fetch_live()
        reply.headers = {"Content-Type": "application/json"}
        reply.read.return_value = b"x" * (Y.MAX_BYTES + 1)
        with (
            patch("urllib.request.build_opener", return_value=opener),
            self.assertRaisesRegex(ValueError, "too large"),
        ):
            Y.fetch_live()

    def test_no_actual_power_events_are_generated(self):
        """Adapter output must contain forecasts only, no outage events."""

        data = Y.parse_schedule(response(), "10.1", NOW, FETCHED)
        self.assertFalse(hasattr(data, "voltage"))
        self.assertFalse(hasattr(data, "actual_power"))
        self.assertFalse(hasattr(data, "outage_event"))


if __name__ == "__main__":
    unittest.main()
