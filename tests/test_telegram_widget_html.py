"""Static safety and size checks for the Telegram message widget prototype."""

from __future__ import annotations

import json
import unittest
import zlib
from html.parser import HTMLParser
from pathlib import Path

import telegram_widget_data as DATA

HTML = Path(__file__).resolve().parents[1] / "widgets" / "voltage_status.html"


class MarkupCollector(HTMLParser):
    """Collect element names and unsafe outbound HTML attributes."""

    def __init__(self):
        """Initialize an empty markup inspection."""

        super().__init__()
        self.tags = []
        self.attributes = []

    def handle_starttag(self, tag, attrs):
        """Track elements and attributes for a small static policy audit."""

        self.tags.append(tag)
        self.attributes.extend(attrs)


class WidgetMarkupTests(unittest.TestCase):
    """Prevent the preview from breaking size and no-network limits."""

    def test_size_limits(self):
        """Meet the documented Telegram compressed and raw HTML caps."""

        raw = HTML.read_bytes()
        self.assertLessEqual(len(raw), 32768)
        self.assertLessEqual(len(zlib.compress(raw, 9)), 4096)

    def test_no_external_assets_or_embedded_requests(self):
        """Keep the HTML self-contained; updates only arrive from host."""

        html = HTML.read_text(encoding="utf-8")
        parser = MarkupCollector()
        parser.feed(html)
        for forbidden in ("iframe", "form", "input", "video", "audio"):
            self.assertNotIn(forbidden, parser.tags)
        for key, value in parser.attributes:
            self.assertNotIn(key, ("src", "href", "action", "srcset"))
        for forbidden in (
            "fetch(",
            "XMLHttpRequest",
            "WebSocket",
            "localStorage",
            "sendWidget(",
            "eval(",
        ):
            self.assertNotIn(forbidden, html)
        self.assertIn("publicDataChanged", html)
        self.assertIn("watchPublicData", html)

    def test_preview_never_claims_power_from_a_schedule(self):
        """Forecast source and live-measurement disclaimer stay visible."""

        html = HTML.read_text(encoding="utf-8")
        self.assertIn("Неофициальный прогноз", html)
        self.assertIn("План не подтверждает наличие электричества", html)
        self.assertIn("Неизвестно, есть ли электричество", html)

    def test_widget_payload_is_bounded_and_has_no_network_urls(self):
        """A long forecast still fits the Telegram shared-data field."""

        health = {
            "connectivity": "online",
            "voltage": 220,
            "frequency": 50,
            "last_success": 1794092400.0,
        }
        payload = DATA.widget_public_data(
            health,
            group="10.1",
            now=1794092405.0,
            bars="O" * 48,
            day="2026-11-08",
            forecast_updated=1794092400.0,
        )
        self.assertLessEqual(len(payload.encode("utf-8")), 1024)
        self.assertNotIn("http", payload)
        self.assertEqual(json.loads(payload)["g"], "10.1")


if __name__ == "__main__":
    unittest.main()
