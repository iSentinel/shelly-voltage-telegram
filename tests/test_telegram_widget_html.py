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
        self.assertIn("setInterval(render,10000)", html)
        self.assertIn('onEvent("activated",render)', html)

    def test_multiphase_tiles_hide_for_single_phase_meters(self):
        """Only Pro 3EM previews display L2 and L3 in the HTML card."""

        html = HTML.read_text(encoding="utf-8")
        for marker in (
            'id="extraPhases"',
            'id="phaseL2"',
            'id="phaseL3"',
            'class="metrics secondary" hidden',
            "data.p.L2",
            "data.p.L3",
            "now-item.t<=60",
            ".metrics[hidden]{display:none}",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, html)

    def test_schedule_expires_with_age_and_midnight(self):
        """Stop showing an expired schedule even when the bubble stays open."""

        html = HTML.read_text(encoding="utf-8")
        self.assertIn("now-data.u>7200", html)
        self.assertIn("now>=data.e", html)
        self.assertIn("data.schema!==1", html)
        self.assertIn("if(bars===drawn)return", html)

    def test_distinct_yasno_source_in_all_bot_languages(self):
        """A YASNO forecast must never be labelled as a community mirror."""

        html = HTML.read_text(encoding="utf-8")
        for expected in (
            "YASNO · графік ДТЕК Київ",
            "YASNO · график ДТЭК Киев",
            "YASNO · DTEK Kyiv forecast",
            'data.src==="yasno"?T[22]:T[16]',
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, html)

        """Hide the grid, time labels, and group unless bars are valid."""

        html = HTML.read_text(encoding="utf-8")
        for marker in (
            'id="timeline" class="timeline" hidden',
            'id="axis" class="axis" hidden',
            'id="legend" class="legend" hidden',
            'id="group" class="hint" hidden',
            ".timeline[hidden],.axis[hidden],.legend[hidden]{display:none}",
            '$("timeline").hidden=!scheduleVisible',
            '$("axis").hidden=!scheduleVisible',
            '$("legend").hidden=!scheduleVisible',
            '$("group").hidden=!hasGroup',
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, html)

    def test_all_three_bot_languages_have_ui_strings(self):
        """Translate the card by the chosen bot language, not by assumption."""

        html = HTML.read_text(encoding="utf-8")
        for item in (
            "Напруга",
            "Напряжение",
            "Voltage",
            "Неофіційний",
            "Неофициальный",
            "Unofficial",
        ):
            with self.subTest(item=item):
                self.assertIn(item, html)
        self.assertIn('data.l==="string"', html)

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
