"""Offline acceptance of idempotent, locked YASNO refresh jobs."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from test_yasno_schedule import response

from yasno_cache import atomic_write, build_cache

KYIV = ZoneInfo("Europe/Kyiv")
ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "yasno_refresh", ROOT / "scripts" / "yasno-refresh.py"
)
assert SPEC is not None and SPEC.loader is not None
REFRESH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REFRESH)


def valid_cache(path: Path, age_s: int) -> None:
    """Create a sealed cache that represents today's valid publication."""

    now = datetime.now(KYIV)
    atomic_write(
        str(path),
        build_cache(response(now), now - timedelta(seconds=age_s)),
    )


class YasnoRefreshTests(unittest.TestCase):
    """Verify safe, one-shot, noninteractive refresh outcomes."""

    def test_fresh_cache_skips_fetch(self):
        """An existing successful fetch suppresses repeated requests."""

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "schedule.json"
            valid_cache(target, 120)
            before = target.read_bytes()
            with patch.object(REFRESH, "fetch_to_cache") as fetch:
                outcome = REFRESH.refresh_once(str(target), "10.1", 900)
            self.assertIn("SKIPPED", outcome)
            fetch.assert_not_called()
            self.assertEqual(target.read_bytes(), before)

    def test_due_cache_fetches_once(self):
        """A 15-minute-old published cache may be refreshed once."""

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "schedule.json"
            valid_cache(target, 901)
            day = datetime.now(KYIV).date().isoformat()
            stub = SimpleNamespace(group="10.1", day=day)
            with patch.object(
                REFRESH, "fetch_to_cache", return_value=stub
            ) as fetch:
                outcome = REFRESH.refresh_once(str(target), "10.1", 900)
            self.assertIn("UPDATED", outcome)
            fetch.assert_called_once_with(str(target), "10.1")
            self.assertTrue(Path(str(target) + ".lock").exists())

    def test_invalid_cache_is_eligible_for_retry(self):
        """Invalid schedules are never trusted due to their file age."""

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "schedule.json"
            target.write_text("corrupt")
            day = datetime.now(KYIV).date().isoformat()
            with patch.object(
                REFRESH,
                "fetch_to_cache",
                return_value=SimpleNamespace(group="10.1", day=day),
            ) as fetch:
                result = REFRESH.refresh_once(str(target), "10.1", 900)
            self.assertIn("UPDATED", result)
            fetch.assert_called_once()

    def test_live_failure_preserves_last_good_data(self):
        """A transient HTTP failure may not destroy the previous cache."""

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "schedule.json"
            valid_cache(target, 1000)
            old = target.read_bytes()
            with (
                patch.object(
                    REFRESH,
                    "fetch_to_cache",
                    side_effect=ValueError("source unavailable"),
                ),
                self.assertRaisesRegex(ValueError, "unavailable"),
            ):
                REFRESH.refresh_once(str(target), "10.1", 900)
            self.assertEqual(target.read_bytes(), old)

    def test_concurrent_job_fails_without_fetching(self):
        """Only one process can enter the network refresh section."""

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "schedule.json"
            with (
                REFRESH.exclusive_lock(str(target)),
                patch.object(REFRESH, "fetch_to_cache") as fetch,
                self.assertRaisesRegex(ValueError, "another"),
            ):
                REFRESH.refresh_once(str(target), "10.1", 900)
            fetch.assert_not_called()

    def test_lock_permissions_and_symlink_protected(self):
        """A tampered or linked lock cannot acquire refresh rights."""

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "schedule.json"
            lock = Path(str(target) + ".lock")
            lock.write_text("not secret")
            lock.chmod(0o666)
            with self.assertRaisesRegex(ValueError, "writable"):
                REFRESH.refresh_once(str(target), "10.1", 900)
            lock.unlink()
            lock.symlink_to(Path(directory) / "other")
            with self.assertRaisesRegex(ValueError, "symlink"):
                REFRESH.refresh_once(str(target), "10.1", 900)

    def test_invalid_interval_rejected_without_writes(self):
        """Do not allow an unsafe fast cadence or a huge interval."""

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "schedule.json"
            for seconds in (0, 1, 299, 7201):
                with (
                    self.subTest(seconds=seconds),
                    self.assertRaisesRegex(ValueError, "interval"),
                ):
                    REFRESH.refresh_once(str(target), "10.1", seconds)
            self.assertFalse(target.exists())

    def test_dry_run_missing_cache_does_not_touch_disk_or_network(self):
        """Preview a due refresh with no lock and no HTTP requests."""

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "schedule.json"
            args = ["yasno-refresh.py", "--dry-run", "--cache", str(target)]
            stdout = io.StringIO()
            with (
                patch.object(sys, "argv", args),
                patch.object(REFRESH, "fetch_to_cache") as fetch,
                contextlib.redirect_stdout(stdout),
            ):
                code = REFRESH.main()
            self.assertEqual(code, 0)
            self.assertIn("FETCH_DUE", stdout.getvalue())
            self.assertIn("no_network=True", stdout.getvalue())
            fetch.assert_not_called()
            self.assertFalse(target.exists())
            self.assertFalse(Path(str(target) + ".lock").exists())

    def test_status_reports_ready_or_missing_read_only(self):
        """Status is a monitorable exit code, not a network fetch."""

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "schedule.json"
            args = ["yasno-refresh.py", "--status", "--cache", str(target)]
            with (
                patch.object(sys, "argv", args),
                contextlib.redirect_stdout(io.StringIO()) as output,
            ):
                self.assertEqual(REFRESH.main(), 1)
            self.assertIn("FAIL STATUS", output.getvalue())
            valid_cache(target, 20)
            with (
                patch.object(sys, "argv", args),
                contextlib.redirect_stdout(io.StringIO()) as output,
            ):
                self.assertEqual(REFRESH.main(), 0)
            self.assertIn("PASS STATUS", output.getvalue())

    def test_no_service_or_bot_side_effects(self):
        """This module remains an isolated, optional read-only producer."""

        source = (ROOT / "scripts" / "yasno-refresh.py").read_text()
        for forbidden in (
            "telegram.send(",
            "docker restart",
            "systemctl start",
            "Shelly.Set",
            "sqlite3.connect",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
