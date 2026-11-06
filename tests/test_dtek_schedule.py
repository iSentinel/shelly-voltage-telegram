"""Offline tests for the optional DTEK outage schedule adapter."""

import unittest
from unittest.mock import Mock, patch

import dtek_schedule as dtek


class DtekScheduleTests(unittest.TestCase):
    """Verify half-hour periods and safe failures."""

    def test_extract_fact_without_greedy_match(self):
        html = '<script>fact = {"data": {"x": 1}}; other = {};</script>'
        self.assertEqual(dtek.extract_schedule(html)["data"], {"x": 1})

    def test_half_hour_windows_are_merged(self):
        hours = {str(hour): "yes" for hour in range(1, 25)}
        hours["1"] = "first"
        hours["2"] = "no"
        hours["3"] = "second"
        self.assertEqual(
            dtek.outage_periods(hours),
            [("00:00", "00:30"), ("01:00", "02:00"), ("02:30", "03:00")],
        )

    def test_missing_hour_rejected(self):
        with self.assertRaisesRegex(ValueError, "incomplete"):
            dtek.outage_periods({"1": "no"})

    def test_unconfigured_does_not_fetch(self):
        with patch.object(dtek, "get_schedule") as fetch:
            self.assertIn("DTEK_REGION", dtek.format_schedule("ru", "", ""))
            fetch.assert_not_called()

    def test_fetch_failure_is_not_no_outages(self):
        with patch.object(
            dtek, "get_schedule", side_effect=ValueError("bad")
        ):
            text = dtek.format_schedule("ru", "k", "1.2")
            self.assertIn("недоступен", text)
            self.assertNotIn("отключений нет", text)

    def test_invalid_group_rejected_before_network(self):
        with self.assertRaisesRegex(ValueError, "invalid"):
            dtek.get_schedule("k", "../etc/passwd")

    def test_valid_schedule_uses_kyiv_today(self):
        day = dtek.datetime.now(
            dtek.ZoneInfo("Europe/Kyiv")
        ).strftime("%Y-%m-%d")
        hours = {str(hour): "yes" for hour in range(1, 25)}
        hours["1"] = "no"
        import json
        data = {"data": {day: {"GPV1.2": hours}}}
        response = Mock(text=f"<script>fact = {json.dumps(data)};</script>")
        response.raise_for_status.return_value = None
        session = Mock()
        session.get.return_value = response
        self.assertEqual(
            dtek.get_schedule("k", "1.2", session)[1],
            [("00:00", "01:00")],
        )


if __name__ == "__main__":
    unittest.main()
