"""No-network QA acceptance for atomic YASNO cache provenance metadata."""

from __future__ import annotations

import json
import stat
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from test_yasno_schedule import response

import yasno_cache as C
from yasno_schedule import parse_schedule

KYIV = ZoneInfo("Europe/Kyiv")
NOW = datetime(2026, 11, 8, 2, tzinfo=KYIV)
FETCHED = NOW - timedelta(seconds=30)


class YasnoCacheTests(unittest.TestCase):
    """Keep provider metadata distinct from a crypto-signed assertion."""

    def test_cache_roundtrip_and_permissions(self):
        """A valid envelope round-trips with private permissions."""

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "yasno.json"
            C.atomic_write(str(path), C.build_cache(response(), FETCHED))
            payload, captured = C.read_verified_cache(str(path))
            self.assertEqual(payload, response())
            self.assertEqual(captured, FETCHED)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(list(path.parent.glob(".yasno-*")), [])

    def test_source_url_change_fails_closed(self):
        """Only the direct pinned YASNO endpoint is accepted."""

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "yasno.json"
            data = C.build_cache(response(), FETCHED)
            data["source_url"] = "https://attacker.example/"
            C.atomic_write(str(path), data)
            with self.assertRaisesRegex(ValueError, "provider metadata"):
                C.read_verified_cache(str(path))

    def test_checksum_detects_payload_mutation(self):
        """A stored checksum guards accidental/tampered file corruption."""

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "yasno.json"
            data = C.build_cache(response(), FETCHED)
            data["payload"]["10.1"]["today"]["slots"][1]["end"] = 710
            C.atomic_write(str(path), data)
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                C.read_verified_cache(str(path))

    def test_unprotected_or_symlinked_files_are_rejected(self):
        """Read-only preview must not follow links or writable caches."""

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "yasno.json"
            C.atomic_write(str(path), C.build_cache(response(), FETCHED))
            link = Path(tmp) / "alias.json"
            link.symlink_to(path)
            with self.assertRaisesRegex(ValueError, "symlink"):
                C.read_verified_cache(str(link))
            with self.assertRaisesRegex(ValueError, "symlink"):
                C.atomic_write(str(link), C.build_cache(response(), FETCHED))
            path.chmod(0o666)
            with self.assertRaisesRegex(ValueError, "writable"):
                C.read_verified_cache(str(path))
            path.chmod(0o600)

    def test_schema_and_malformed_envelope_rejected(self):
        """A valid JSON file is not necessarily a valid YASNO cache."""

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "yasno.json"
            C.atomic_write(str(path), {"schema": 2})
            with self.assertRaisesRegex(ValueError, "schema"):
                C.read_verified_cache(str(path))
            path.write_text("not valid json")
            with self.assertRaises(json.JSONDecodeError):
                C.read_verified_cache(str(path))

    def test_retrieval_age_still_validated_after_read(self):
        """A valid checksum cannot make an old retrieval current."""

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "yasno.json"
            old = NOW - timedelta(seconds=7201)
            C.atomic_write(str(path), C.build_cache(response(), old))
            payload, captured = C.read_verified_cache(str(path))
            with self.assertRaisesRegex(ValueError, "capture"):
                parse_schedule(payload, "10.1", NOW, captured)

    def test_failure_does_not_replace_last_good_cache(self):
        """An unpublished live response must preserve the old cache."""

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "yasno.json"
            C.atomic_write(str(path), C.build_cache(response(), FETCHED))
            before = path.read_bytes()
            invalid = response()
            invalid["10.1"]["today"]["status"] = "WaitingForSchedule"
            with (
                patch(
                    "yasno_cache.fetch_live",
                    return_value=(invalid, datetime.now(timezone.utc)),
                ),
                self.assertRaisesRegex(ValueError, "not published"),
            ):
                C.fetch_to_cache(str(path), "10.1")
            self.assertEqual(path.read_bytes(), before)

    def test_fetch_to_cache_uses_only_mocked_direct_provider(self):
        """Validate one complete source before atomically caching it."""

        now = datetime.now(KYIV)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "yasno.json"
            with patch(
                "yasno_cache.fetch_live", return_value=(response(now), now)
            ) as live:
                result = C.fetch_to_cache(str(path), "10.1")
            live.assert_called_once()
            self.assertEqual(result.day, now.date().isoformat())
            self.assertTrue(path.is_file())
            self.assertEqual(
                C.read_verified_cache(str(path))[0], response(now)
            )

    def test_parent_and_payload_are_bounded(self):
        """Do not create unexpected output directories or huge payloads."""

        with tempfile.TemporaryDirectory() as tmp:
            wrong = Path(tmp) / "missing" / "yasno.json"
            with self.assertRaisesRegex(ValueError, "parent"):
                C.atomic_write(str(wrong), C.build_cache(response(), FETCHED))
            huge = {"data": "x" * (C.MAX_BYTES + 1)}
            with self.assertRaisesRegex(ValueError, "size"):
                C.build_cache(huge, FETCHED)


if __name__ == "__main__":
    unittest.main()
