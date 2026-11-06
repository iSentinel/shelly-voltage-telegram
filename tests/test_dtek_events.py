"""Test conservative DTEK emergency classification without live network."""

from __future__ import annotations

import unittest

from dtek_events import (
    ProviderEvent,
    classify_house,
    event_key,
    format_advisory,
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

    def test_advisory_is_not_a_power_loss_claim(self):
        """Make it clear that the provider is only an external source."""

        message = format_advisory(classify_house(example()), "uk")
        self.assertIn("ДТЕК повідомляє", message)
        self.assertIn("не показники Shelly", message)
        self.assertNotIn("Світло зникло", message)

    def test_unsafe_provider_markup_is_escaped(self):
        """Never render untrusted DTEK reason as Telegram HTML markup."""

        event = classify_house(
            example(sub_type="Аварійні <script>& небезпека")
        )
        message = format_advisory(event, "ru")
        self.assertIn("&lt;script&gt;&amp;", message)
        self.assertNotIn("<script>", message)

    def test_unknown_does_not_display_untrusted_details(self):
        """An unrecognized reason must not be promoted as confirmed."""

        event = classify_house(example(sub_type="Неизвестный код"))
        message = format_advisory(event, "ru")
        self.assertIn("неизвестна", message)
        self.assertNotIn("Неизвестный код", message)

    def test_no_outage_is_a_provider_report_only(self):
        """No DTEK advisory never equals evidence of live voltage."""

        empty = example(sub_type="", start_date="", end_date="", type="")
        message = format_advisory(classify_house(empty), "en")
        self.assertIn("DTEK reports no active outage", message)
        self.assertIn("Not a Shelly measurement", message)

    def test_all_supported_locales_render(self):
        """Render titles and disclaimers for all supported languages."""

        for language, expected in (
            ("ru", "Причина"),
            ("uk", "Причина"),
            ("en", "Reason"),
        ):
            with self.subTest(language=language):
                message = format_advisory(classify_house(example()), language)
                self.assertIn(expected, message)
                self.assertIn("<b>", message)
                self.assertIn("</i>", message)

    def test_long_values_are_bounded(self):
        """Keep a maliciously long provider message within Telegram size."""

        event = ProviderEvent(
            "emergency",
            reason="А" * 10000,
            started="x" * 10000,
            expected_end="x" * 10000,
            source_updated="x" * 10000,
        )
        message = format_advisory(event, "uk")
        self.assertLess(len(message), 1500)
        self.assertNotIn("А" * 161, message)

    def test_unknown_category_fails_closed(self):
        """A new provider category must not imply a known outage type."""

        message = format_advisory(
            ProviderEvent("unexpected", reason="wrong"), "en"
        )
        self.assertIn("unknown", message)
        self.assertNotIn("wrong", message)

    def test_no_address_or_token_stored(self):
        event = ProviderEvent("unknown")
        self.assertNotIn("street", repr(event).lower())
        self.assertNotIn("token", repr(event).lower())


if __name__ == "__main__":
    unittest.main()
