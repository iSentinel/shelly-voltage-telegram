"""QA: unpublished YASNO forecast never rewrites last accepted handoff."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from test_yasno_handoff import fixture
from test_yasno_refresh import REFRESH
from test_yasno_schedule import response

import yasno_handoff as HANDOFF
from yasno_cache import read_verified_cache

KYIV = ZoneInfo("Europe/Kyiv")


class YasnoUnpublishedRecoverySequenceTests(unittest.TestCase):
    """Run actual cache and handoff code without network or Telegram."""

    def prepare(self, folder: str):
        """Create an old-but-valid schedule and recipient copy."""
        source, target, gid = fixture(folder, age_s=1000)
        accepted = HANDOFF.handoff(
            str(source), str(target), "10.1", gid, "install"
        )
        self.assertIn("UPDATED", accepted)
        return source, target, gid

    def test_unpublished_source_preserves_both_protected_caches(self):
        """WaitingForSchedule cannot replace a previously accepted day."""
        with tempfile.TemporaryDirectory() as temp:
            source, target, gid = self.prepare(temp)
            old_source, old_target = source.read_bytes(), target.read_bytes()
            old_mtime = target.stat().st_mtime_ns
            unavailable = response(datetime.now(KYIV))
            unavailable["10.1"]["today"]["status"] = "WaitingForSchedule"
            with (
                patch(
                    "yasno_cache.fetch_live",
                    return_value=(unavailable, datetime.now(KYIV)),
                ),
                self.assertRaisesRegex(ValueError, "not published"),
            ):
                REFRESH.refresh_once(str(source), "10.1", 900)
            self.assertEqual(source.read_bytes(), old_source)
            self.assertEqual(target.read_bytes(), old_target)
            self.assertEqual(target.stat().st_mtime_ns, old_mtime)
            self.assertIn(
                "valid recipient cache",
                HANDOFF.handoff(
                    str(source), str(target), "10.1", gid, "status"
                ),
            )

    def test_successful_retry_handoffs_only_new_verified_cache(self):
        """A later published schedule replaces both caches atomically."""
        with tempfile.TemporaryDirectory() as temp:
            source, target, gid = self.prepare(temp)
            old = target.read_bytes()
            now = datetime.now(KYIV)
            with patch(
                "yasno_cache.fetch_live",
                return_value=(response(now), now),
            ) as fetch:
                result = REFRESH.refresh_once(str(source), "10.1", 900)
            fetch.assert_called_once_with()
            self.assertIn("UPDATED", result)
            self.assertEqual(
                HANDOFF.handoff(
                    str(source), str(target), "10.1", gid, "install"
                ).split()[0],
                "UPDATED",
            )
            self.assertNotEqual(old, target.read_bytes())
            self.assertEqual(
                read_verified_cache(str(source))[1],
                read_verified_cache(str(target))[1],
            )

    def test_repeated_handoff_does_not_create_a_new_update(self):
        """Unchanged provider snapshot cannot trigger repeated publishing."""
        with tempfile.TemporaryDirectory() as temp:
            source, target, gid = self.prepare(temp)
            old = target.read_bytes()
            old_mtime = target.stat().st_mtime_ns
            result = HANDOFF.handoff(
                str(source), str(target), "10.1", gid, "install"
            )
            self.assertIn("SKIPPED", result)
            self.assertEqual(target.read_bytes(), old)
            self.assertEqual(target.stat().st_mtime_ns, old_mtime)
            self.assertFalse(list(Path(temp).rglob(".yasno-export-*")))


if __name__ == "__main__":
    unittest.main()
