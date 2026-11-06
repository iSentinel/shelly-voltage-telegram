"""Test conservative DTEK emergency classification without live network."""

from __future__ import annotations

import unittest

from dtek_events import (
    ProviderEvent,
    classify_house,
    event_key,
    parse_house_response,
)


def example(**overrides) -> dict:
    """Prepare a provider-shaped response fixture."""

    data = {
        "sub_type": "Аварійні ремонтні роботи",
        "start_date": "10:30 17.11.2026",
        "end_date": "18:00 17.11.2026",
        "type": "",
    }
    data.update(overrides)
    return data


class DtekEventTests(unittest.TestCase):
    """Protect against false outage and Telegram-notification diagnoses."""

    def test_emergency_reason_detected(self):
        event = classify_house(example())
        self.assertEqual(event.category, "emergency")
        self.assertIn("10:30", event.started)

    def test_emergency_alternative_label(self):
        event = classify_house(example(sub_type="Екстрені відключення"))
        self.assertEqual(event.category, "emergency")

    def test_planned_status_is_separate(self):
        event = classify_house(example(sub_type="Планові роботи"))
        self.assertEqual(event.category, "planned")

    def test_stabilization_status_is_planned(self):
        event = classify_house(example(sub_type="Стабілізаційний графік"))
        self.assertEqual(event.category, "planned")

    def test_unknown_reason_does_not_imply_emergency(self):
        event = classify_house(example(sub_type="Ремонтні роботи"))
        self.assertEqual(event.category, "unknown")

    def test_missing_fields_are_unknown_not_no_outage(self):
        self.assertEqual(classify_house({}).category, "unknown")
        self.assertEqual(classify_house(None).category, "unknown")
        wrong = classify_house(example(type=None))
        self.assertEqual(wrong.category, "unknown")

    def test_explicitly_empty_fields_are_none(self):
        empty = example(sub_type="", start_date="", end_date="")
        event = classify_house(empty)
        self.assertEqual(event.category, "none")

    def test_unlabeled_dates_are_unknown(self):
        event = classify_house(example(sub_type=""))
        self.assertEqual(event.category, "unknown")

    def test_wrong_house_is_unknown(self):
        reply = {"data": {"17": example()}}
        self.assertEqual(parse_house_response(reply, "21").category, "unknown")

    def test_exact_house_and_source_timestamp(self):
        reply = {
            "data": {"17": example()},
            "updateTimestamp": "17.11.2026 10:50",
        }
        event = parse_house_response(reply, "17")
        self.assertEqual(event.category, "emergency")
        self.assertEqual(event.source_updated, "17.11.2026 10:50")

    def test_provider_timestamp_change_keeps_dedupe_key(self):
        first = classify_house(example(), "17.11.2026 10:50")
        second = classify_house(example(), "17.11.2026 11:00")
        self.assertEqual(event_key(first), event_key(second))

    def test_end_time_change_updates_event_key(self):
        first = classify_house(example())
        second = classify_house(example(end_date="19:00 17.11.2026"))
        self.assertNotEqual(event_key(first), event_key(second))

    def test_normalization_removes_newlines(self):
        event = classify_house(example(sub_type="Аварійні\n  роботи"))
        self.assertNotIn("\n", event.reason)

    def test_no_address_or_token_stored(self):
        event = ProviderEvent("unknown")
        self.assertNotIn("street", repr(event).lower())
        self.assertNotIn("token", repr(event).lower())


if __name__ == "__main__":
    unittest.main()
