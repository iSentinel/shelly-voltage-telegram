"""Validate systemd identity checks without installing users."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import call, patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "yasno_systemd_account",
    ROOT / "scripts/yasno-systemd-account-preflight.py",
)
assert SPEC is not None and SPEC.loader is not None
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)


class AccountGuardTests(unittest.TestCase):
    """Confirm account- and filesystem-based rollout blockers."""

    def test_missing_service_account_fails_closed(self):
        """Do not infer a valid systemd identity from a numeric UID."""

        with (
            patch.object(QA.pwd, "getpwnam", side_effect=KeyError),
            self.assertRaises(KeyError),
        ):
            QA.service_account("shelly-yasno")

    def test_account_requires_unprivileged_non_login(self):
        """Reject root, the consumer UID and interactive logins."""

        for uid, shell in (
            (0, "/usr/sbin/nologin"),
            (10001, "/usr/sbin/nologin"),
            (10002, "/bin/bash"),
        ):
            entry = SimpleNamespace(pw_uid=uid, pw_gid=10002, pw_shell=shell)
            with (
                self.subTest(uid=uid, shell=shell),
                patch.object(QA.pwd, "getpwnam", return_value=entry),
                self.assertRaises(ValueError),
            ):
                QA.service_account("shelly-yasno")
        valid = SimpleNamespace(
            pw_uid=10002, pw_gid=10002, pw_shell="/usr/sbin/nologin"
        )
        with patch.object(QA.pwd, "getpwnam", return_value=valid):
            self.assertEqual(
                QA.service_account("shelly-yasno"), (10002, 10002)
            )

    def test_directory_checks_mode_and_symlinks(self):
        """A writable or linked producer directory must fail."""

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "private"
            path.mkdir(mode=0o700)
            QA.directory(path, os.geteuid(), os.getegid(), 0o700)
            path.chmod(0o777)
            with self.assertRaisesRegex(ValueError, "mode"):
                QA.directory(path, os.geteuid(), os.getegid(), 0o700)
            path.chmod(0o700)
            alias = Path(tmp) / "linked"
            alias.symlink_to(path, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symlink"):
                QA.directory(alias, os.geteuid(), os.getegid(), 0o700)

    def test_file_checks_mode_and_hardlinks(self):
        """Never treat a linked or world-readable cache as protected."""

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "yasno.json"
            path.write_text("synthetic", encoding="utf-8")
            path.chmod(0o600)
            QA.file(path, os.geteuid(), os.getegid(), 0o600)
            path.chmod(0o644)
            with self.assertRaisesRegex(ValueError, "mode"):
                QA.file(path, os.geteuid(), os.getegid(), 0o600)
            path.chmod(0o600)
            linked = Path(tmp) / "hardlink.json"
            os.link(path, linked)
            with self.assertRaisesRegex(ValueError, "single regular"):
                QA.file(path, os.geteuid(), os.getegid(), 0o600)

    def test_bootstrap_checks_directories_without_initial_files(self):
        """Clean installation can pass before the first YASNO refresh."""

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(QA, "service_account", return_value=(10002, 10002)),
            patch.object(QA, "directory") as directories,
            patch.object(QA, "file") as files,
        ):
            result = QA.verify(
                "shelly-yasno",
                Path(tmp) / "producer",
                Path(tmp) / "reader",
                10001,
                "bootstrap",
            )
        self.assertIn("phase=bootstrap", result)
        self.assertEqual(directories.call_count, 2)
        files.assert_not_called()

    def test_live_phase_requires_both_cache_files(self):
        """No missing initial cache may pass as live readiness."""

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(QA, "service_account", return_value=(10002, 10002)),
            patch.object(QA, "directory"),
            self.assertRaisesRegex(ValueError, "not been provisioned"),
        ):
            QA.verify(
                "shelly-yasno",
                Path(tmp) / "producer",
                Path(tmp) / "reader",
                10001,
                "live",
            )

    def test_bootstrap_rejects_present_but_insecure_file(self):
        """Bootstrap must never ignore an existing unsafe cache file."""

        with tempfile.TemporaryDirectory() as tmp:
            producer = Path(tmp) / "producer"
            producer.mkdir()
            reader = Path(tmp) / "reader"
            reader.mkdir()
            cached = producer / "yasno.json"
            cached.write_text("synthetic", encoding="utf-8")
            cached.chmod(0o666)
            with (
                patch.object(
                    QA,
                    "service_account",
                    return_value=(os.geteuid(), os.getegid()),
                ),
                patch.object(QA, "directory"),
                self.assertRaisesRegex(ValueError, "mode"),
            ):
                QA.verify("shelly-yasno", producer, reader, 10001, "bootstrap")

    def test_bootstrap_rejects_dangling_symlinks(self):
        """Missing target of a symlink cannot count as missing cache."""

        with tempfile.TemporaryDirectory() as tmp:
            producer = Path(tmp) / "producer"
            producer.mkdir()
            reader = Path(tmp) / "reader"
            reader.mkdir()
            (producer / "yasno.json").symlink_to("/nonexistent/yasno")
            with (
                patch.object(
                    QA, "service_account", return_value=(10002, 10002)
                ),
                patch.object(QA, "directory"),
                self.assertRaisesRegex(ValueError, "not been provisioned"),
            ):
                QA.verify("shelly-yasno", producer, reader, 10001, "bootstrap")

    def test_unknown_phase_rejected_before_account_lookup(self):
        """Typos must not weaken the cache verification policy."""

        with (
            patch.object(QA, "service_account") as account,
            self.assertRaisesRegex(ValueError, "phase"),
        ):
            QA.verify(
                "shelly-yasno",
                Path("/producer"),
                Path("/reader"),
                10001,
                "skip",
            )
        account.assert_not_called()

    def test_missing_cache_file_is_not_a_green_release_gate(self):
        """A provisioned directory without data is insufficient."""

        with (
            tempfile.TemporaryDirectory() as tmp,
            self.assertRaisesRegex(ValueError, "not been provisioned"),
        ):
            QA.file(Path(tmp) / "yasno.json", 10002, 10002, 0o600)

    def test_verify_respects_producer_reader_separation(self):
        """Check all required owners and modes before claiming PASS."""

        with (
            patch.object(QA, "service_account", return_value=(10002, 10002)),
            patch.object(QA, "directory") as directory,
            patch.object(QA, "file") as file,
        ):
            result = QA.verify(
                "shelly-yasno", Path("/producer"), Path("/reader"), 10001
            )
        self.assertIn("cache_modes_ok=true", result)
        self.assertEqual(
            directory.call_args_list,
            [
                call(Path("/producer"), 10002, 10002, 0o700),
                call(Path("/reader"), 0, 10001, 0o750),
            ],
        )
        self.assertEqual(file.call_count, 2)

    def test_dry_run_is_independent_of_live_account_and_files(self):
        """No account lookup or filesystem mutation in dry-run."""

        with (
            patch.object(QA.pwd, "getpwnam") as account,
            patch.object(QA, "verify") as verify,
            patch.object(
                QA.argparse.ArgumentParser,
                "parse_args",
                return_value=SimpleNamespace(dry_run=True, check=False),
            ),
            contextlib.redirect_stdout(io.StringIO()) as result,
        ):
            self.assertEqual(QA.main(), 0)
        self.assertIn("no_files_changed=true", result.getvalue())
        account.assert_not_called()
        verify.assert_not_called()

    def test_check_reports_missing_user_without_leaking_details(self):
        """The absent actual service account must not be marked PASS."""

        args = SimpleNamespace(
            dry_run=False,
            check=True,
            account="shelly-yasno",
            producer="/var/lib/shelly-yasno",
            recipient="/var/lib/shelly-yasno-reader",
            reader_gid=10001,
            phase="live",
        )
        with (
            patch.object(
                QA.argparse.ArgumentParser,
                "parse_args",
                return_value=args,
            ),
            patch.object(QA, "verify", side_effect=KeyError),
            contextlib.redirect_stderr(io.StringIO()) as result,
        ):
            self.assertEqual(QA.main(), 1)
        self.assertIn("FAIL SYSTEMD_ACCOUNT KeyError", result.getvalue())


if __name__ == "__main__":
    unittest.main()
