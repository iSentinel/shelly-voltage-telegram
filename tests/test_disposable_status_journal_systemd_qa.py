"""Unit-level contract for the optional isolated real-systemd journal QA."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/disposable-status-journal-systemd-qa.py"
)
SPEC = importlib.util.spec_from_file_location("systemd_journal_qa", SCRIPT)
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)


class JournalSystemdUnitTests(unittest.TestCase):
    """Use mock systemd operations and invented private journal states."""

    def setUp(self) -> None:
        """Construct disposable scratch fixture for one test."""
        temp = tempfile.TemporaryDirectory(prefix="journal-prestart-unit-")
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.model = QA.load_model()

    def invoke(self, *args: str) -> int:
        """Return CLI exit without exposing production operations."""
        with (
            patch.object(sys, "argv", ["qa", *args]),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            return QA.main()

    def test_default_cli_is_inert(self) -> None:
        """No explicit opt-in means no transient systemd unit."""
        with patch.object(QA, "load_model", side_effect=AssertionError):
            self.assertEqual(self.invoke(), 0)

    def test_invalid_confirmation_denied(self) -> None:
        """Generic yes must not launch any systemd command."""
        with patch.object(QA, "load_model", side_effect=AssertionError):
            self.assertEqual(
                self.invoke("--run-disposable", "--confirm", "yes"), 2
            )

    def test_relative_model_path(self) -> None:
        """The module path is relative to repository, not an SSH host."""
        self.assertTrue(QA.MODEL.is_file())
        self.assertEqual(QA.MODEL.parent, SCRIPT.parent)

    def test_pending_published_is_not_committed(self) -> None:
        """Even matching JSON cannot pass prestart before journal commit."""
        QA.prepare(self.model, self.folder, "published_pending")
        status = self.model.classify(self.folder, *self.model.trusted())
        self.assertEqual(status, "RECONCILE_REQUIRED")

    def test_missing_pending_still_not_committed(self) -> None:
        """Missing pending must not allow ExecStart without journal."""
        QA.prepare(self.model, self.folder, "published_missing")
        status = self.model.classify(self.folder, *self.model.trusted())
        self.assertEqual(status, "RECONCILE_REQUIRED")

    def test_committed_status_allows_prestart(self) -> None:
        """An explicitly committed synthetic transaction passes."""
        QA.prepare(self.model, self.folder, "committed")
        self.assertEqual(
            self.model.classify(self.folder, *self.model.trusted()),
            "COMMITTED",
        )

    def test_corrupt_public_after_commit_blocks(self) -> None:
        """A corrupt public file overrides any committed journal."""
        QA.prepare(self.model, self.folder, "corrupt")
        self.assertEqual(
            self.model.classify(self.folder, *self.model.trusted()),
            "BLOCKED_INVALID_METADATA",
        )

    def test_systemd_runner_uses_private_network(self) -> None:
        """The command targets only a transient disposable unit."""
        with patch.object(QA.subprocess, "run") as mocked:
            mocked.return_value = SimpleNamespace(returncode=1)
            QA.run_unit(self.folder, "published_missing")
        argv = mocked.call_args.args[0]
        self.assertEqual(argv[0], "systemd-run")
        self.assertIn("--collect", argv)
        self.assertIn("--wait", argv)
        self.assertIn("PrivateNetwork=yes", argv)
        self.assertIn("ProtectSystem=strict", argv)

    def test_fail_closed_if_prestart_unexpectedly_succeeds(self) -> None:
        """An unexpected successful ExecStart on uncommitted state fails."""
        with (
            patch.object(
                QA,
                "run_unit",
                return_value=SimpleNamespace(
                    returncode=0, stdout="", stderr=""
                ),
            ),
            self.assertRaisesRegex(RuntimeError, "SYSTEMD_START_MISMATCH"),
        ):
            QA.run_case(self.model, "published_pending")

    def test_fail_closed_if_committed_cannot_start(self) -> None:
        """An unexpected denied start after synthetic commit is detected."""
        with (
            patch.object(
                QA,
                "run_unit",
                return_value=SimpleNamespace(
                    returncode=1, stdout="", stderr=""
                ),
            ),
            self.assertRaisesRegex(RuntimeError, "SYSTEMD_START_MISMATCH"),
        ):
            QA.run_case(self.model, "committed")


if __name__ == "__main__":
    unittest.main()
