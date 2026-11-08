"""Isolated permission and integrity acceptance for the YASNO bot handoff."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from test_yasno_schedule import response

import yasno_handoff as H
from yasno_cache import atomic_write, build_cache, read_verified_cache

KYIV = ZoneInfo("Europe/Kyiv")


def recipient_gid() -> int:
    """Use the caller's group for portable CI, bot GID on root QA."""

    return os.getegid() if os.getegid() else 10001


def fixture(tmp: str, age_s: int = 60) -> tuple[Path, Path, int]:
    """Create an isolated source and protected recipient directory."""

    root = Path(tmp)
    source_dir = root / "source"
    dest_dir = root / "recipient"
    source_dir.mkdir(mode=0o700)
    dest_dir.mkdir(mode=0o750)
    gid = recipient_gid()
    if dest_dir.stat().st_gid != gid:
        os.chown(dest_dir, os.geteuid(), gid)
    source = source_dir / "yasno.json"
    captured = datetime.now(KYIV) - timedelta(seconds=age_s)
    atomic_write(
        str(source), build_cache(response(datetime.now(KYIV)), captured)
    )
    return source, dest_dir / "yasno.json", gid


class YasnoHandoffTests(unittest.TestCase):
    """Ensure a bot can read, but not write, validated forecast content."""

    def test_dry_run_does_not_create_files(self):
        """A dry-run only validates source and target directory."""

        with tempfile.TemporaryDirectory() as tmp:
            source, target, gid = fixture(tmp)
            result = H.handoff(
                str(source), str(target), "10.1", gid, "dry-run"
            )
            self.assertIn("no_writes=True", result)
            self.assertFalse(target.exists())

    def test_install_file_and_repeated_install_are_idempotent(self):
        """Atomic export preserves original retrieval metadata."""

        with tempfile.TemporaryDirectory() as tmp:
            source, target, gid = fixture(tmp)
            result = H.handoff(
                str(source), str(target), "10.1", gid, "install"
            )
            self.assertIn("UPDATED", result)
            info = target.stat()
            self.assertEqual(stat.S_IMODE(info.st_mode), 0o640)
            self.assertEqual((info.st_uid, info.st_gid), (os.geteuid(), gid))
            self.assertEqual(stat.S_IMODE(target.parent.stat().st_mode), 0o750)
            self.assertEqual(
                read_verified_cache(str(source))[1],
                read_verified_cache(str(target))[1],
            )
            old_mtime = target.stat().st_mtime_ns
            again = H.handoff(str(source), str(target), "10.1", gid, "install")
            self.assertIn("SKIPPED", again)
            self.assertEqual(target.stat().st_mtime_ns, old_mtime)
            self.assertEqual(list(target.parent.glob(".yasno-export-*")), [])

    def test_status_is_read_only_and_requires_valid_target(self):
        """A missing receiver is not equivalent to an accepted handoff."""

        with tempfile.TemporaryDirectory() as tmp:
            source, target, gid = fixture(tmp)
            with self.assertRaises(FileNotFoundError):
                H.handoff(str(source), str(target), "10.1", gid, "status")
            H.handoff(str(source), str(target), "10.1", gid, "install")
            result = H.handoff(str(source), str(target), "10.1", gid, "status")
            self.assertIn("valid recipient cache", result)

    def test_stale_source_does_not_replace_recipient(self):
        """Old provider data must not be re-exported as a new acquisition."""

        with tempfile.TemporaryDirectory() as tmp:
            source, target, gid = fixture(tmp)
            H.handoff(str(source), str(target), "10.1", gid, "install")
            original = target.read_bytes()
            old = datetime.now(KYIV) - timedelta(seconds=7201)
            atomic_write(
                str(source), build_cache(response(datetime.now(KYIV)), old)
            )
            with self.assertRaisesRegex(ValueError, "capture"):
                H.handoff(str(source), str(target), "10.1", gid, "install")
            self.assertEqual(target.read_bytes(), original)

    def test_unpublished_source_does_not_change_recipient(self):
        """WaitingForSchedule cannot become a certain planned event."""

        with tempfile.TemporaryDirectory() as tmp:
            source, target, gid = fixture(tmp)
            H.handoff(str(source), str(target), "10.1", gid, "install")
            original = target.read_bytes()
            invalid = response(datetime.now(KYIV))
            invalid["10.1"]["today"]["status"] = "WaitingForSchedule"
            atomic_write(str(source), build_cache(invalid, datetime.now(KYIV)))
            with self.assertRaisesRegex(ValueError, "not published"):
                H.handoff(str(source), str(target), "10.1", gid, "install")
            self.assertEqual(target.read_bytes(), original)

    def test_corruption_rejected_before_install(self):
        """A matching filename is not proof of trusted YASNO content."""

        with tempfile.TemporaryDirectory() as tmp:
            source, target, gid = fixture(tmp)
            envelope = json.loads(source.read_text())
            envelope["payload"]["10.1"]["today"]["status"] = "fake"
            source.write_text(json.dumps(envelope))
            with self.assertRaisesRegex(ValueError, "checksum"):
                H.handoff(str(source), str(target), "10.1", gid, "install")
            self.assertFalse(target.exists())

    def test_insecure_directory_and_symlink_rejected(self):
        """Never export to a directory whose members can replace files."""

        with tempfile.TemporaryDirectory() as tmp:
            source, target, gid = fixture(tmp)
            target.parent.chmod(0o770)
            with self.assertRaisesRegex(ValueError, "ownership or mode"):
                H.handoff(str(source), str(target), "10.1", gid, "install")
            target.parent.chmod(0o750)
            alias = Path(tmp) / "linked"
            alias.symlink_to(target.parent, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symlink"):
                H.handoff(
                    str(source),
                    str(alias / "yasno.json"),
                    "10.1",
                    gid,
                    "install",
                )

    def test_bad_target_metadata_is_rejected(self):
        """An attacker-writable or multiply linked cache fails closed."""

        with tempfile.TemporaryDirectory() as tmp:
            source, target, gid = fixture(tmp)
            H.handoff(str(source), str(target), "10.1", gid, "install")
            target.chmod(0o666)
            with self.assertRaisesRegex(ValueError, "mode"):
                H.handoff(str(source), str(target), "10.1", gid, "install")
            target.chmod(0o640)
            alias = target.parent / "alias.json"
            os.link(target, alias)
            with self.assertRaisesRegex(ValueError, "regular file"):
                H.handoff(str(source), str(target), "10.1", gid, "install")

    def test_invalid_gid_does_not_create_any_file(self):
        """Reject surprising root or oversized recipient identifiers."""

        with tempfile.TemporaryDirectory() as tmp:
            source, target, _ = fixture(tmp)
            for gid in (0, -1, 65536):
                with (
                    self.subTest(gid=gid),
                    self.assertRaisesRegex(ValueError, "gid"),
                ):
                    H.handoff(str(source), str(target), "10.1", gid, "install")
            self.assertFalse(target.exists())

    def test_newer_recipient_cannot_be_replaced_by_older_capture(self):
        """Preserve successful fresh acquisition even with racing writers."""

        with tempfile.TemporaryDirectory() as tmp:
            source, target, gid = fixture(tmp, age_s=20)
            H.handoff(str(source), str(target), "10.1", gid, "install")
            previous = target.read_bytes()
            older = datetime.now(KYIV) - timedelta(seconds=120)
            atomic_write(
                str(source), build_cache(response(datetime.now(KYIV)), older)
            )
            with self.assertRaisesRegex(ValueError, "newer recipient"):
                H.handoff(str(source), str(target), "10.1", gid, "install")
            self.assertEqual(target.read_bytes(), previous)

    def test_parallel_writer_is_rejected(self):
        """An existing export lock prevents another writer taking over."""

        with tempfile.TemporaryDirectory() as tmp:
            source, target, gid = fixture(tmp)
            with (
                H.exclusive_lock(target),
                self.assertRaisesRegex(ValueError, "another handoff"),
            ):
                H.handoff(str(source), str(target), "10.1", gid, "install")
            self.assertFalse(target.exists())

    def test_insecure_lock_cannot_be_used(self):
        """Reject a linked or attacker-writable lock before installation."""

        with tempfile.TemporaryDirectory() as tmp:
            source, target, gid = fixture(tmp)
            lock = target.with_name(target.name + ".lock")
            lock.write_text("lock", encoding="utf-8")
            lock.chmod(0o666)
            with self.assertRaisesRegex(ValueError, "lock"):
                H.handoff(str(source), str(target), "10.1", gid, "install")
            lock.unlink()
            lock.symlink_to(source)
            with self.assertRaisesRegex(ValueError, "lock"):
                H.handoff(str(source), str(target), "10.1", gid, "install")

    def test_compose_override_is_read_only_and_off_by_default(self):
        """Never publish an example that silently enables channel posting."""

        root = Path(__file__).resolve().parents[1]
        example = (root / "deploy/compose.yasno-reader.example.yml").read_text(
            encoding="utf-8"
        )
        actions = (root / ".github/workflows/ci.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn('TELEGRAM_YASNO_STATUS_ENABLED: "0"', example)
        self.assertIn("/var/lib/shelly-yasno-reader:/run/yasno:ro", example)
        self.assertIn("YASNO_TRUSTED_CACHE_FILE", example)
        self.assertIn(
            "docker compose -f compose.yaml -f "
            "deploy/compose.yasno-reader.example.yml config -q",
            actions,
        )

    def test_source_cannot_be_recipient(self):
        """Never chmod or replace the original producer cache."""

        with tempfile.TemporaryDirectory() as tmp:
            source, _, gid = fixture(tmp)
            with self.assertRaisesRegex(ValueError, "paths must differ"):
                H.handoff(str(source), str(source), "10.1", gid, "install")


if __name__ == "__main__":
    unittest.main()
