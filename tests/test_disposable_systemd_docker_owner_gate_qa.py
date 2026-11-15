"""Unit tests for transient systemd -> flock B SH -> Docker QA gate."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/disposable-systemd-docker-owner-gate-qa.py"
)
SPEC = importlib.util.spec_from_file_location("systemd_docker_qa", SCRIPT)
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)


def result(code: int) -> SimpleNamespace:
    """Return deterministic mocked subprocess result."""
    return SimpleNamespace(returncode=code, stdout="", stderr="test")


class RealSystemdDockerGateTests(unittest.TestCase):
    """Default unit runs never start Docker or systemd."""

    def setUp(self) -> None:
        """Create only private local scratch data."""
        temp = tempfile.TemporaryDirectory(prefix="systemd-docker-unit-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.owner = QA.load_owner()

    def invoke(self, *args: str) -> int:
        """Execute default CLI with no external command calls."""
        with (
            patch.object(sys, "argv", ["qa", *args]),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            return QA.main()

    def test_default_inert(self) -> None:
        """The default must never inspect or change the running host."""
        with patch.object(QA, "load_owner", side_effect=AssertionError):
            self.assertEqual(self.invoke(), 0)

    def test_confirmation_denied(self) -> None:
        """A generic yes must not launch any systemd QA unit."""
        with patch.object(QA, "load_owner", side_effect=AssertionError):
            self.assertEqual(
                self.invoke("--run-disposable", "--confirm", "yes"), 2
            )

    def test_owner_module_local(self) -> None:
        """The source owner guard is repo-local, never production."""
        self.assertEqual(QA.OWNER.parent, SCRIPT.parent)
        self.assertTrue(QA.OWNER.is_file())

    def test_transient_unit_has_shared_B_gate(self) -> None:
        """Systemd starts the Docker command only through flock B SH."""
        with patch.object(
            QA.subprocess, "run", return_value=result(1)
        ) as mocked:
            QA.launch_unit(self.owner, self.root, "qa-unit", "unit-deny")
        argv = mocked.call_args.args[0]
        self.assertEqual(argv[0], "systemd-run")
        self.assertIn("--collect", argv)
        self.assertIn("--wait", argv)
        self.assertIn("PrivateNetwork=yes", argv)
        self.assertIn("ProtectSystem=strict", argv)
        self.assertIn("/usr/bin/flock", argv)
        self.assertIn("--shared", argv)
        self.assertIn("--nonblock", argv)
        self.assertIn("no", argv)
        self.assertIn("--restart", argv)

    def test_prepared_scratch_contains_only_qa_lock(self) -> None:
        """A private mode 0600 synthetic lock is the only authority."""
        with patch.object(QA.os, "chown"):
            QA.prepare(self.root)
        self.assertEqual(
            stat.S_IMODE((self.root / "maintenance.lock").stat().st_mode),
            0o600,
        )
        self.assertTrue((self.root / "events").is_dir())
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)

    def test_no_restart_writer_completion(self) -> None:
        """The control container exits once without Docker restart."""
        events = self.root / "events"
        events.mkdir()
        (events / "restarts.log").write_text("attempt\n")
        with patch.object(self.owner, "get_state", return_value=(0, "exited")):
            proof = QA.await_exited(self.owner, self.root, "qa-unit")
        self.assertEqual(proof["restart_count"], 0)
        self.assertEqual(proof["synthetic_attempts"], 1)

    def test_B_denied_then_free_starts(self) -> None:
        """The guard prevents a start during B EX but allows after release."""
        with (
            patch.object(
                QA, "launch_unit", side_effect=[result(1), result(0)]
            ),
            patch.object(self.owner, "command", return_value=result(1)),
            patch.object(
                QA, "await_exited", return_value={"restart_count": 0}
            ),
            patch.object(self.owner, "cleanup_container") as clean,
            patch.object(QA.os, "chown"),
        ):
            proof = QA.rehearse(self.owner)
        self.assertTrue(proof["B_EX_denied_systemd_start"])
        self.assertTrue(proof["B_unlocked_allowed_systemd_start"])
        clean.assert_called_once()

    def test_unexpected_B_bypass_fails_closed(self) -> None:
        """Unexpected systemd success under B EX is a QA failure."""
        with (
            patch.object(QA, "launch_unit", return_value=result(0)),
            patch.object(self.owner, "command", return_value=result(1)),
            patch.object(self.owner, "cleanup_container") as clean,
            patch.object(QA.os, "chown"),
            self.assertRaisesRegex(RuntimeError, "B_EX_ALLOWED"),
        ):
            QA.rehearse(self.owner)
        clean.assert_called_once()

    def test_failed_allowed_start_fails_closed(self) -> None:
        """A denied start after B release is not considered success."""
        with (
            patch.object(
                QA, "launch_unit", side_effect=[result(1), result(1)]
            ),
            patch.object(self.owner, "command", return_value=result(1)),
            patch.object(self.owner, "cleanup_container") as clean,
            patch.object(QA.os, "chown"),
            self.assertRaisesRegex(RuntimeError, "B_FREE_START_FAILED"),
        ):
            QA.rehearse(self.owner)
        clean.assert_called_once()


if __name__ == "__main__":
    unittest.main()
