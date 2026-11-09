"""Tests for read-only storage and backup headroom checks."""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from collections import namedtuple
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

PATH = (
    Path(__file__).resolve().parents[1] / "scripts/shelly-storage-preflight.py"
)
SPEC = importlib.util.spec_from_file_location("storage_preflight", PATH)
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)

Usage = namedtuple("Usage", "total used free")


def args(root: Path, backup: Path, warn: float = 1024, fail: float = 256):
    """Build a disposable CLI fixture."""

    return argparse.Namespace(
        data_dir=root,
        backup_dir=backup,
        warn_free_mib=warn,
        fail_free_mib=fail,
        max_backup_age_hours=36,
    )


class StoragePreflightTests(unittest.TestCase):
    """Always use temporary files; never use a real monitoring database."""

    def setUp(self):
        """Create a temporary database and backup directory."""

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.data = self.root / "data"
        self.backup = self.root / "backups"
        self.data.mkdir()
        self.backup.mkdir()
        self.now = 100000.0
        file = self.backup / "voltage-sample.sqlite3.gz"
        file.write_bytes(b"fixture")
        import os

        os.utime(file, (self.now - 3600, self.now - 3600))

    def inspect(self, free_mib: float):
        """Return a report from a mocked filesystem capacity."""

        mib = CHECK.MIB
        with patch.object(
            CHECK.shutil,
            "disk_usage",
            return_value=Usage(
                4 * 1024 * mib, (4096 - free_mib) * mib, free_mib * mib
            ),
        ):
            return CHECK.check_storage(args(self.data, self.backup), self.now)

    def test_plenty_of_space_and_fresh_backup_pass(self):
        """Normal storage and fresh backups require no warning."""

        report = self.inspect(2048)
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["backup_count"], 1)
        self.assertEqual(report["filesystem_writes"], 0)

    def test_low_space_warns_before_critical_failure(self):
        """The current 820 MiB headroom should be WARN, not PASS."""

        report = self.inspect(820)
        self.assertEqual(report["status"], "warn")
        self.assertIn("low_free_space", report["reasons"])

    def test_critical_space_is_fail(self):
        """A filesystem with less than 256 MiB free is a hard failure."""

        self.assertEqual(self.inspect(128)["status"], "fail")

    def test_missing_backups_fail(self):
        """No matching backup is a failure regardless of free space."""

        for file in self.backup.iterdir():
            file.unlink()
        report = self.inspect(2048)
        self.assertEqual(report["status"], "fail")
        self.assertIn("backup_missing", report["reasons"])

    def test_stale_backup_warns(self):
        """A 48-hour-old backup is not fresh enough for daily cadence."""

        import os

        backup = next(self.backup.iterdir())
        os.utime(backup, (self.now - 172800, self.now - 172800))
        report = self.inspect(2048)
        self.assertEqual(report["status"], "warn")
        self.assertIn("backup_stale", report["reasons"])

    def test_unrelated_files_and_symlinks_not_counted(self):
        """Backup directory content cannot disguise missing real backups."""

        original = next(self.backup.iterdir())
        original.rename(self.root / "real.gz")
        (self.backup / "voltage-link.sqlite3.gz").symlink_to(
            self.root / "real.gz"
        )
        (self.backup / "unrelated.sqlite3.gz").write_bytes(b"other")
        report = self.inspect(2048)
        self.assertEqual(report["status"], "fail")

    def test_empty_matching_backup_never_yields_pass(self):
        """A zero-byte archive with a valid name is not a usable backup."""

        for path in self.backup.iterdir():
            path.write_bytes(b"")
        result = self.inspect(2048)
        self.assertEqual(result["status"], "fail")
        self.assertIn("backup_missing", result["reasons"])
        self.assertIn("backup_metadata_untrusted", result["reasons"])

    def test_future_dated_backup_does_not_hide_stale_copy(self):
        """An impossible clock timestamp cannot mask an old backup."""

        import os

        stale = next(self.backup.iterdir())
        os.utime(stale, (self.now - 48 * 3600,) * 2)
        future = self.backup / "voltage-future.sqlite3.gz"
        future.write_bytes(b"sample")
        os.utime(future, (self.now + 3600,) * 2)
        result = self.inspect(2048)
        self.assertEqual(result["status"], "warn")
        self.assertIn("backup_stale", result["reasons"])
        self.assertIn("backup_metadata_untrusted", result["reasons"])
        self.assertEqual(result["backup_count"], 1)
        self.assertEqual(result["ignored_backup_entries"], 1)

    def test_only_future_backup_fails_not_false_pass(self):
        """A future-only archive leaves no credible valid backup."""

        import os

        future = next(self.backup.iterdir())
        os.utime(future, (self.now + 3600,) * 2)
        result = self.inspect(2048)
        self.assertEqual(result["status"], "fail")
        self.assertIn("backup_missing", result["reasons"])
        self.assertEqual(result["backup_count"], 0)

    def test_small_clock_skew_remains_tolerated(self):
        """Allow up to five minutes of normal backup clock skew."""

        import os

        recent = next(self.backup.iterdir())
        os.utime(recent, (self.now + 240,) * 2)
        result = self.inspect(2048)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["latest_backup_age_hours"], 0)

    def test_symlink_with_real_backup_does_not_claim_clean_pass(self):
        """A symlink entry cannot silently be promoted as valid evidence."""

        (self.backup / "voltage-link.sqlite3.gz").symlink_to(
            next(self.backup.iterdir())
        )
        result = self.inspect(2048)
        self.assertEqual(result["status"], "warn")
        self.assertEqual(result["backup_count"], 1)
        self.assertIn("backup_metadata_untrusted", result["reasons"])

    def test_invalid_thresholds_fail_early(self):
        """Reject nonsense thresholds before reading disk metadata."""

        for warn, fail in ((100, 200), (float("inf"), 20), (20, 0)):
            with (
                self.subTest(warn=warn, fail=fail),
                self.assertRaises(ValueError),
            ):
                CHECK.validate_args(args(self.data, self.backup, warn, fail))

    def test_dry_run_has_no_file_reads(self):
        """No filesystem check happens when dry run was requested."""

        output = io.StringIO()
        with (
            patch.object(sys, "argv", ["storage", "--dry-run", "--json"]),
            patch.object(
                CHECK.shutil,
                "disk_usage",
                side_effect=AssertionError("unexpected access"),
            ),
            redirect_stdout(output),
        ):
            self.assertEqual(CHECK.main(), 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "dry_run")

    def test_cli_warn_and_help_exit_codes(self):
        """Operators can distinguish a warning from a hard failure."""

        output = io.StringIO()
        with (
            patch.object(sys, "argv", ["storage", "--json"]),
            patch.object(
                CHECK,
                "check_storage",
                return_value={"status": "warn", "reasons": ["low_free_space"]},
            ),
            redirect_stdout(output),
        ):
            self.assertEqual(CHECK.main(), 3)
        with (
            patch.object(sys, "argv", ["storage", "--help"]),
            redirect_stdout(io.StringIO()),
            self.assertRaises(SystemExit) as finish,
        ):
            CHECK.parser().parse_args()
        self.assertEqual(finish.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
