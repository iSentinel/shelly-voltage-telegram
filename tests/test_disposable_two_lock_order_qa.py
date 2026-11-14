"""Validate isolated advisory lock ordering without production effects."""

from __future__ import annotations

import contextlib
import fcntl
import importlib.util
import io
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/disposable-two-lock-order-qa.py"
)
SPEC = importlib.util.spec_from_file_location("isolated_lock_order", SCRIPT)
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)


class DisposableTwoLockTests(unittest.TestCase):
    """All file locks are new, private fixtures owned by this test."""

    def setUp(self) -> None:
        """Allocate two independent private locks in a disposable dir."""
        directory = tempfile.TemporaryDirectory(
            prefix="svt-two-lock-unit-", dir="/tmp"
        )
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.maintenance = self.root / "maintenance.lock"
        self.updater = self.root / "updater.lock"
        QA.create_lock(self.maintenance)
        QA.create_lock(self.updater)

    def cli(self, *args: str) -> tuple[int, str]:
        """Capture the standalone command without starting services."""
        output = io.StringIO()
        with (
            patch.object(sys, "argv", [str(SCRIPT), *args]),
            contextlib.redirect_stdout(output),
        ):
            return QA.main(), output.getvalue()

    def test_default_is_inert(self) -> None:
        """No actions or subprocesses without explicit opt-in."""
        with patch.object(QA, "run_disposable", side_effect=AssertionError):
            code, output = self.cli()
        self.assertEqual(code, 0)
        self.assertIn("DRY-RUN", output)

    def test_dry_run_dominates_action(self) -> None:
        """Explicit dry-run must always remain a no-op."""
        with patch.object(QA, "run_disposable", side_effect=AssertionError):
            code, _ = self.cli("--dry-run", "--run-disposable")
        self.assertEqual(code, 0)

    def test_confirmation_exact(self) -> None:
        """Generic approvals must never run even disposable work."""
        with patch.object(QA, "run_disposable", side_effect=AssertionError):
            code, output = self.cli("--run-disposable", "--confirm", "yes")
        self.assertEqual(code, 2)
        self.assertIn("FAIL", output)

    def test_help_available(self) -> None:
        """Help does not create files or contact Docker."""
        for option in ("-h", "--help"):
            with self.subTest(option=option):
                process = subprocess.run(
                    [sys.executable, str(SCRIPT), option],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                self.assertEqual(process.returncode, 0)
                self.assertIn("--confirm", process.stdout)

    def test_private_mode_and_distinct_inodes(self) -> None:
        """Private locks are regular 0600 files, not shared inodes."""
        for path in (self.maintenance, self.updater):
            info = path.stat()
            self.assertTrue(stat.S_ISREG(info.st_mode))
            self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
        self.assertNotEqual(
            self.maintenance.stat().st_ino, self.updater.stat().st_ino
        )

    def test_existing_lock_cannot_be_overwritten(self) -> None:
        """Existing files are never truncated or followed."""
        with self.assertRaises(FileExistsError):
            QA.create_lock(self.maintenance)

    def test_failed_assumption_blocks(self) -> None:
        """A mismatch must raise, never create an authorization."""
        with self.assertRaisesRegex(RuntimeError, "QA_BLOCKED"):
            QA.require(False, "QA_BLOCKED")

    def test_free_exclusive_lock(self) -> None:
        """Another process can lock a free updater mutex."""
        self.assertEqual(QA.probe(self.updater, "ex"), "ACQUIRED")

    def test_free_shared_lock(self) -> None:
        """Another process can lock a free maintenance file."""
        self.assertEqual(QA.probe(self.maintenance, "sh"), "ACQUIRED")

    def test_shared_blocks_exclusive_same_inode(self) -> None:
        """A supervisor shared lock cannot be upgraded by a child."""
        with self.maintenance.open("rb") as lock:
            fcntl.flock(lock, fcntl.LOCK_SH)
            self.assertEqual(QA.probe(self.maintenance, "ex"), "BLOCKED")

    def test_shared_allows_separate_updater_mutex(self) -> None:
        """Independent updater mutex is available during prestart."""
        with self.maintenance.open("rb") as lock:
            fcntl.flock(lock, fcntl.LOCK_SH)
            self.assertEqual(QA.probe(self.updater, "ex"), "ACQUIRED")

    def test_shared_allows_shared(self) -> None:
        """Two reader guards can coexist when no updater runs."""
        with self.maintenance.open("rb") as lock:
            fcntl.flock(lock, fcntl.LOCK_SH)
            self.assertEqual(QA.probe(self.maintenance, "sh"), "ACQUIRED")

    def test_exclusive_updater_fences_guard(self) -> None:
        """An updater holding EXCLUSIVE refuses a new shared guard."""
        with self.maintenance.open("rb") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.assertEqual(QA.probe(self.maintenance, "sh"), "BLOCKED")

    def test_split_protocol_four_cases(self) -> None:
        """All planned primitive ordering expectations hold."""
        result = QA.split_lock_cases(self.maintenance, self.updater)
        self.assertEqual(result["same_inode_prestart"], "BLOCKED")
        self.assertEqual(result["separate_updater_prestart"], "ACQUIRED")
        self.assertEqual(result["updater_excludes_supervisor"], "BLOCKED")

    def test_real_two_process_cycle(self) -> None:
        """Both independent processes block while retaining first locks."""
        result = QA.circular_wait(self.root, self.maintenance, self.updater)
        self.assertEqual(result["first_process"], "SECOND_BLOCKED")
        self.assertEqual(result["second_process"], "SECOND_BLOCKED")

    def test_full_private_runner_denies_all_production_actions(self) -> None:
        """True process QA explicitly grants no live authority."""
        result = QA.run_disposable()
        self.assertEqual(result["status"], "PASS_PRIVATE_TWO_LOCK_ORDER_QA")
        for key in (
            "production_modified",
            "docker_or_systemd_started",
            "production_lock_files_opened",
            "boot_start_authorized",
            "source_install_authorized",
            "telegram_photo_authorized",
        ):
            self.assertFalse(result[key])

    def test_full_runner_removes_temporary_locks(self) -> None:
        """No orphaned child lock process or file may remain."""
        before = set(Path("/var/tmp").glob("svt-private-lock-order-*"))
        QA.run_disposable()
        after = set(Path("/var/tmp").glob("svt-private-lock-order-*"))
        self.assertEqual(after, before)

    def test_cli_failed_qa_exit(self) -> None:
        """A failed private test must return code 3 rather than PASS."""
        with patch.object(
            QA, "run_disposable", side_effect=RuntimeError("BAD_QA")
        ):
            code, message = self.cli(
                "--run-disposable", "--confirm", QA.CONFIRM
            )
        self.assertEqual(code, 3)
        self.assertIn("BAD_QA", message)

    def test_cli_jsonl(self) -> None:
        """CLI writes a result, not a claim of prod authorization."""
        with patch.object(
            QA,
            "run_disposable",
            return_value={"status": "PASS_PRIVATE_TWO_LOCK_ORDER_QA"},
        ):
            code, output = self.cli(
                "--run-disposable", "--confirm", QA.CONFIRM, "--jsonl"
            )
        self.assertEqual(code, 0)
        self.assertIn('"status": "PASS_PRIVATE_TWO_LOCK_ORDER_QA"', output)


if __name__ == "__main__":
    unittest.main()
