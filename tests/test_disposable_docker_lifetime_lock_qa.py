"""Unit contracts for detached Docker runtime lifetime lock gap QA."""

from __future__ import annotations

import contextlib
import fcntl
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
    / "scripts/disposable-docker-lifetime-lock-qa.py"
)
SPEC = importlib.util.spec_from_file_location("docker_lifetime_qa", SCRIPT)
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)


def completed(code: int = 0, output: str = "") -> SimpleNamespace:
    """Return only invented subprocess status."""
    return SimpleNamespace(returncode=code, stdout=output, stderr="error")


class LifetimeGapContractTests(unittest.TestCase):
    """No test launches a real Docker container or a systemd unit."""

    def setUp(self) -> None:
        """Build a private fixture with an invented shared B inode."""
        temp = tempfile.TemporaryDirectory(prefix="lifetime-unit-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / "maintenance.lock").touch(mode=0o600)
        self.owner = QA.import_owner()

    def invoke(self, *args: str) -> int:
        """Use default inert CLI with captured status text."""
        with (
            patch.object(sys, "argv", ["qa", *args]),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            return QA.main()

    def test_default_is_inert(self) -> None:
        """No explicit opt-in means no owner access."""
        with patch.object(QA, "import_owner", side_effect=AssertionError):
            self.assertEqual(self.invoke(), 0)

    def test_exact_confirmation_needed(self) -> None:
        """Generic confirmation never permits real QA."""
        with patch.object(QA, "import_owner", side_effect=AssertionError):
            self.assertEqual(
                self.invoke("--run-disposable", "--confirm", "yes"), 2
            )

    def test_owner_module_is_local(self) -> None:
        """QA cannot unexpectedly source a live installed updater."""
        self.assertEqual(QA.OWNER_PATH.parent, SCRIPT.parent)
        self.assertTrue(QA.OWNER_PATH.exists())

    def test_worker_is_inert_and_no_network(self) -> None:
        """The proof worker only sleeps without Shelly or Telegram."""
        self.assertEqual(QA.WORKER, "import time; time.sleep(50)")
        value = QA.args_for_writer(self.owner, self.root, "qa-unit")
        self.assertEqual(value[-1], QA.WORKER)
        self.assertEqual(value[value.index("--network") + 1], "none")
        self.assertEqual(value[value.index("--restart") + 1], "no")

    def test_unknown_container_template_refused(self) -> None:
        """Unexpected template shape is not silently executed."""
        with (
            patch.object(
                self.owner, "container_args", return_value=["bad", "-c"]
            ),
            self.assertRaisesRegex(RuntimeError, "UNKNOWN_DOCKER_QA"),
        ):
            QA.args_for_writer(self.owner, self.root, "qa-unit")

    def test_detached_systemd_unit_uses_B_sh(self) -> None:
        """Status-only unit locks B SH only around detached docker."""
        with patch.object(QA, "run", return_value=completed()) as mocked:
            QA.run_detached_unit(self.owner, self.root, "qa-unit", "test")
        cmd = mocked.call_args.args[0]
        self.assertEqual(cmd[0], "systemd-run")
        self.assertIn("--collect", cmd)
        self.assertIn("PrivateNetwork=yes", cmd)
        self.assertIn("ProtectSystem=strict", cmd)
        self.assertIn("/usr/bin/flock", cmd)
        self.assertIn("--shared", cmd)
        self.assertIn("--nonblock", cmd)
        self.assertIn("-d", cmd)
        self.assertIn("--restart", cmd)

    def test_running_requires_no_restart(self) -> None:
        """Current Docker state must be running with policy no."""
        with patch.object(
            self.owner, "command", return_value=completed(0, "true|no\n")
        ):
            self.assertTrue(QA.running(self.owner, "qa-unit"))
        with patch.object(
            self.owner,
            "command",
            return_value=completed(0, "true|unless-stopped"),
        ):
            self.assertFalse(QA.running(self.owner, "qa-unit"))

    def test_B_exclusive_available_without_reader(self) -> None:
        """Demonstrate actual EX lock can be acquired on an empty inode."""
        self.assertTrue(QA.acquire_exclusive(self.root))

    def test_B_exclusive_denied_with_B_shared(self) -> None:
        """Independent file descriptor with SH blocks nonblocking EX."""
        with (self.root / "maintenance.lock").open("rb") as holder:
            fcntl.flock(holder, fcntl.LOCK_SH | fcntl.LOCK_NB)
            self.assertFalse(QA.acquire_exclusive(self.root))

    def test_lifetime_wait_uses_same_B(self) -> None:
        """Waiter lease must reference the same private B path."""
        with patch.object(QA.subprocess, "Popen") as mocked:
            QA.start_wait_lease(self.root, "qa-unit")
        cmd = mocked.call_args.args[0]
        self.assertEqual(cmd[0], "/usr/bin/flock")
        self.assertIn("--shared", cmd)
        self.assertIn("docker", " ".join(cmd))
        self.assertIn("wait", cmd)
        self.assertIn(str(self.root / "maintenance.lock"), cmd)

    def test_premature_lease_fails_closed(self) -> None:
        """An already exited lease cannot authorize maintenance."""
        lease = SimpleNamespace(poll=lambda: 1)
        with self.assertRaisesRegex(RuntimeError, "LEASE_TERMINATED"):
            QA.require_lease(self.root, lease)

    def test_reaped_lease_pipes_are_closed(self) -> None:
        """Always communicate to release reaped child pipe descriptors."""
        lease = SimpleNamespace(
            communicate=lambda timeout: ("", ""),
        )
        QA.finish_lease(lease)

    def test_exact_root_mode_only_in_fixture(self) -> None:
        """The B lock is private, and QA does not change system units."""
        self.assertEqual(
            stat.S_IMODE((self.root / "maintenance.lock").stat().st_mode),
            0o600,
        )


if __name__ == "__main__":
    unittest.main()
