"""Offline tests for the cache-only DTEK schedule adapter."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import dtek_schedule as dtek

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/dtek-collect.py"
SPEC = importlib.util.spec_from_file_location("dtek_collector", SCRIPT)
COLLECTOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(COLLECTOR)


def example_fact(now: datetime) -> dict:
    """Build realistic grouped, complete published intervals."""

    day = now.astimezone(dtek.ZoneInfo("Europe/Kyiv")).date().isoformat()
    hours = {str(hour): "yes" for hour in range(1, 25)}
    hours["1"] = "no"
    hours["2"] = "first"
    return {"today": day, "data": {day: {"GPV1.2": hours}}}


def example_cache(now: datetime) -> dict:
    """Build a current schema-v1 cache."""

    return {
        "version": 1,
        "region": "k",
        "fetched_at": now.isoformat(),
        "fact": example_fact(now),
    }


class DtekScheduleTests(unittest.TestCase):
    """Check read-only parsing, freshness and failure boundaries."""

    def setUp(self):
        """Prepare an isolated cache path."""

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "cache.json"
        self.now = datetime.now(timezone.utc)

    def write_cache(self, data):
        """Write a synthetic cache fixture."""

        self.path.write_text(json.dumps(data), encoding="utf-8")

    def test_extract_fact_without_greedy_match(self):
        html = '<script>fact = {"data": {"x": 1}}; other = {};</script>'
        self.assertEqual(dtek.extract_schedule(html)["data"], {"x": 1})

    def test_empty_dtek_page_is_not_an_empty_schedule(self):
        with self.assertRaisesRegex(ValueError, "not found"):
            dtek.extract_schedule("<html></html>")

    def test_incapsula_challenge_is_not_a_valid_schedule(self):
        """HTTP 200 challenge HTML must never be treated as outage data."""

        html = (
            '<html><script src="/_Incapsula_Resource?challenge">'
            "</script></html>"
        )
        with self.assertRaisesRegex(ValueError, "anti-bot"):
            dtek.extract_schedule(html)

    def test_challenge_does_not_replace_last_valid_cache(self):
        """Keep a previous snapshot when the official page blocks access."""

        self.write_cache(example_cache(self.now))
        original = self.path.read_bytes()
        fixture = Path(self.tmp.name) / "blocked.html"
        fixture.write_text("<script src='/_Incapsula_Resource'>")
        argv = [
            "collector",
            "--region",
            "k",
            "--fixture-html",
            str(fixture),
            "--output",
            str(self.path),
        ]
        result = io.StringIO()
        with patch.object(sys, "argv", argv), redirect_stdout(result):
            self.assertEqual(COLLECTOR.main(), 1)
        self.assertIn("provider-challenge", result.getvalue())
        self.assertEqual(self.path.read_bytes(), original)

    def test_unconfigured_is_translated_for_all_languages(self):
        """Avoid Russian-only fallbacks for existing supported locales."""

        expected = {"ru": "укажите", "uk": "укажіть", "en": "set"}
        for language, fragment in expected.items():
            with self.subTest(language=language):
                text = dtek.format_schedule(language, "", "")
                self.assertIn(fragment, text)

    def test_schedule_unavailable_is_translated(self):
        """Unknown provider state must be clear in RU, UK and EN."""

        expected = {
            "ru": "график недоступен",
            "uk": "графік недоступний",
            "en": "schedule unavailable",
        }
        with patch.object(dtek, "get_schedule", side_effect=ValueError):
            for language, fragment in expected.items():
                with self.subTest(language=language):
                    self.assertIn(
                        fragment, dtek.format_schedule(language, "k", "1.2")
                    )

    def test_published_intervals_are_translated(self):
        """Date, group and outage windows remain visible in every language."""

        expected = {
            "ru": "Плановые",
            "uk": "Планові",
            "en": "scheduled",
        }
        with patch.object(
            dtek,
            "get_schedule",
            return_value=("2026-11-06", [("14:00", "18:00")]),
        ):
            for language, fragment in expected.items():
                with self.subTest(language=language):
                    text = dtek.format_schedule(language, "k", "1.2")
                    self.assertIn(fragment.lower(), text.lower())
                    self.assertIn("14:00–18:00", text)
                    self.assertIn("1.2", text)

    def test_no_outages_is_translated_only_for_valid_schedule(self):
        """Empty schedule from a validated source is a distinct state."""

        expected = {
            "ru": "отключений нет",
            "uk": "відключень немає",
            "en": "No outages",
        }
        with patch.object(
            dtek,
            "get_schedule",
            return_value=("2026-11-06", []),
        ):
            for language, fragment in expected.items():
                with self.subTest(language=language):
                    text = dtek.format_schedule(language, "k", "1.2")
                    self.assertIn(fragment, text)

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

    def test_source_failure_not_treated_as_no_outages(self):
        with patch.dict(os.environ, {"DTEK_CACHE_FILE": str(self.path)}):
            text = dtek.format_schedule("ru", "k", "1.2")
        self.assertIn("недоступен", text)
        self.assertNotIn("отключений нет", text)

    def test_invalid_group_rejected(self):
        with self.assertRaisesRegex(ValueError, "invalid"):
            dtek.get_schedule("k", "../etc/passwd", self.path)

    def test_valid_cache_parses_current_day(self):
        self.write_cache(example_cache(self.now))
        day, periods = dtek.get_schedule("k", "1.2", self.path, self.now)
        kyiv_day = self.now.astimezone(dtek.ZoneInfo("Europe/Kyiv")).date()
        self.assertEqual(day, kyiv_day.isoformat())
        self.assertEqual(periods, [("00:00", "01:30")])

    def test_stale_cache_rejected(self):
        old = self.now - timedelta(hours=3)
        self.write_cache(example_cache(old))
        with self.assertRaisesRegex(ValueError, "stale"):
            dtek.get_schedule("k", "1.2", self.path, self.now)

    def test_future_cache_rejected(self):
        data = example_cache(self.now)
        data["fetched_at"] = (self.now + timedelta(minutes=5)).isoformat()
        self.write_cache(data)
        with self.assertRaisesRegex(ValueError, "future"):
            dtek.get_schedule("k", "1.2", self.path, self.now)

    def test_source_day_not_today(self):
        data = example_cache(self.now)
        data["fact"] = example_fact(self.now - timedelta(days=2))
        self.write_cache(data)
        with self.assertRaisesRegex(ValueError, "source date"):
            dtek.get_schedule("k", "1.2", self.path, self.now)

    def test_wrong_region_cache_rejected(self):
        self.write_cache(example_cache(self.now))
        with self.assertRaisesRegex(ValueError, "metadata"):
            dtek.get_schedule("kr", "1.2", self.path, self.now)

    def test_invalid_cache_group_rejected(self):
        data = example_cache(self.now)
        group = next(iter(data["fact"]["data"].values()))
        group["GPV1.2"].pop("24")
        self.write_cache(data)
        with self.assertRaisesRegex(ValueError, "incomplete"):
            dtek.get_schedule("k", "1.2", self.path, self.now)

    def test_cache_size_limit(self):
        self.path.write_text(" " * (dtek.CACHE_MAX_BYTES + 1))
        with self.assertRaisesRegex(ValueError, "too large"):
            dtek.get_schedule("k", "1.2", self.path, self.now)

    def test_collector_accepts_complete_today(self):
        html = f"<script>fact = {json.dumps(example_fact(self.now))};</script>"
        data = COLLECTOR.build_payload("k", html, self.now)
        COLLECTOR.write_cache(self.path, data)
        self.assertEqual(
            dtek.get_schedule("k", "1.2", self.path, self.now)[1],
            [("00:00", "01:30")],
        )
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_browser_collector_rejects_redirected_host(self):
        """Do not follow untrusted hosts after browser navigation."""

        api = MagicMock()
        browser = api.__enter__.return_value.chromium.launch.return_value
        page = browser.new_page.return_value
        page.url = "https://unexpected.example/shutdowns"
        package = types.ModuleType("playwright")
        package.__path__ = []
        sync = types.ModuleType("playwright.sync_api")
        sync.sync_playwright = lambda: api
        with (
            patch.dict(
                sys.modules,
                {"playwright": package, "playwright.sync_api": sync},
            ),
            self.assertRaisesRegex(ValueError, "another host"),
        ):
            COLLECTOR.browser_html("k", 5)
        browser.close.assert_called_once()

    def test_browser_collector_closes_browser_after_valid_page(self):
        """Close the one-shot Chromium browser after collecting HTML."""

        api = MagicMock()
        browser = api.__enter__.return_value.chromium.launch.return_value
        page = browser.new_page.return_value
        page.url = dtek.REGIONS["k"]
        page.content.return_value = "<html>fixture</html>"
        package = types.ModuleType("playwright")
        package.__path__ = []
        sync = types.ModuleType("playwright.sync_api")
        sync.sync_playwright = lambda: api
        with patch.dict(
            sys.modules,
            {"playwright": package, "playwright.sync_api": sync},
        ):
            html = COLLECTOR.browser_html("k", 5)
            self.assertEqual(html, "<html>fixture</html>")
        browser.close.assert_called_once()

    def test_cli_fixture_dry_run_does_not_write(self):
        """Exercise the collector CLI without network or disk output."""

        html = "<script>fact = " + json.dumps(example_fact(self.now))
        fixture = Path(self.tmp.name) / "fixture.html"
        fixture.write_text(html + ";</script>", encoding="utf-8")
        argv = [
            "collector",
            "--region",
            "k",
            "--fixture-html",
            str(fixture),
            "--output",
            str(self.path),
            "--dry-run",
        ]
        with patch.object(sys, "argv", argv):
            self.assertEqual(COLLECTOR.main(), 0)
        self.assertFalse(self.path.exists())

    def test_symlink_cache_rejected(self):
        """The monitor must not follow an untrusted cache symlink."""

        target = Path(self.tmp.name) / "actual.json"
        target.write_text(json.dumps(example_cache(self.now)))
        self.path.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "symlinks"):
            dtek.get_schedule("k", "1.2", self.path, self.now)

    def test_collector_does_not_replace_on_invalid(self):
        self.write_cache(example_cache(self.now))
        original = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "not found"):
            COLLECTOR.build_payload("k", "<html></html>", self.now)
        self.assertEqual(self.path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
