"""Unit checks for inherited B lock under synthetic supervisor SIGKILL."""

from __future__ import annotations

import contextlib
import fcntl
import importlib.util
import io
import signal
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/disposable-inherited-b-lifetime-qa.py"
)
SPEC = importlib.util.spec_from_file_location("inherited_b_qa", SCRIPT)
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)


def completed(code: int = 0, output: str = "") -> SimpleNamespace:
    """Return a safely mocked subprocess result."""
    return SimpleNamespace(returncode=code, stdout=output)


class InheritedBUnitTests(unittest.TestCase):
    """Never launch real workers, Docker or service units from unit tests."""

    def setUp(self) -> None:
        """Create a private, invented root-owned B inode."""
        directory = tempfile.TemporaryDirectory(prefix="inherited-b-unit-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / "maintenance.lock").touch(mode=0o600)

    def invoke(self, *args: str) -> int:
        """Capture a default-inert CLI response."""
        with (
            patch.object(sys, "argv", ["qa", *args]),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            return QA.main()

    def test_default_dry_run(self) -> None:
        """The default may not inspect or change real services."""
        with patch.object(QA, "guest_guard", side_effect=AssertionError):
            self.assertEqual(self.invoke(), 0)

    def test_generic_confirmation_rejected(self) -> None:
        """Only exact isolated consent can launch experimental processes."""
        with patch.object(QA, "guest_guard", side_effect=AssertionError):
            self.assertEqual(
                self.invoke("--run-disposable", "--confirm", "yes"), 2
            )

    def test_nonroot_denied(self) -> None:
        """No actual execution outside privileged offline QA."""
        with (
            patch.object(QA.os, "geteuid", return_value=1000),
            self.assertRaisesRegex(RuntimeError, "NOT_EXACT_OFFLINE_QA"),
        ):
            QA.guest_guard()

    def test_wrong_host_denied(self) -> None:
        """A production hostname must fail before any subprocess."""
        with (
            patch.object(QA.os, "geteuid", return_value=0),
            patch.object(QA.socket, "gethostname", return_value="production"),
            self.assertRaisesRegex(RuntimeError, "NOT_EXACT_OFFLINE_QA"),
        ):
            QA.guest_guard()

    def test_network_interface_denied(self) -> None:
        """An interface beyond lo is not acceptable QA isolation."""
        with (
            patch.object(QA.os, "geteuid", return_value=0),
            patch.object(
                QA.socket,
                "gethostname",
                return_value="shelly-docker-reboot-qa",
            ),
            patch.object(
                QA.socket,
                "if_nameindex",
                return_value=[(1, "lo"), (2, "eth0")],
            ),
            self.assertRaisesRegex(RuntimeError, "EXTERNAL_INTERFACE"),
        ):
            QA.guest_guard()

    def test_original_writer_running_denied(self) -> None:
        """Do not run fault testing with an active QA signed writer."""
        with (
            patch.object(QA.os, "geteuid", return_value=0),
            patch.object(
                QA.socket,
                "gethostname",
                return_value="shelly-docker-reboot-qa",
            ),
            patch.object(QA.socket, "if_nameindex", return_value=[(1, "lo")]),
            patch.object(QA, "cmd", return_value=completed(0, "true|no")),
            self.assertRaisesRegex(RuntimeError, "ORIGINAL_QA_WRITER"),
        ):
            QA.guest_guard()

    def test_original_service_enabled_denied(self) -> None:
        """Refuse any running background QA owner before fault injection."""
        with (
            patch.object(QA.os, "geteuid", return_value=0),
            patch.object(
                QA.socket,
                "gethostname",
                return_value="shelly-docker-reboot-qa",
            ),
            patch.object(QA.socket, "if_nameindex", return_value=[(1, "lo")]),
            patch.object(
                QA,
                "cmd",
                side_effect=[
                    completed(0, "false|no"),
                    completed(0, "enabled"),
                ],
            ),
            self.assertRaisesRegex(RuntimeError, "QA_SYSTEMD_OWNER"),
        ):
            QA.guest_guard()

    def test_real_B_exclusive_without_reader(self) -> None:
        """EX works when the synthetic B inode is unlocked."""
        self.assertTrue(QA.can_get_exclusive(self.root))

    def test_real_B_exclusive_blocked_by_shared(self) -> None:
        """Another fd sharing the inode prevents EX."""
        with (self.root / "maintenance.lock").open("rb") as holder:
            fcntl.flock(holder, fcntl.LOCK_SH | fcntl.LOCK_NB)
            self.assertFalse(QA.can_get_exclusive(self.root))

    def test_synthetic_role_code_is_valid(self) -> None:
        """Test subprocesses are Python only, never real Docker."""
        compile(QA.SUPERVISOR_CODE, "supervisor.py", "exec")
        compile(QA.WORKER_CODE, "writer.py", "exec")
        self.assertIn("pass_fds=pass_fds", QA.SUPERVISOR_CODE)
        self.assertIn("inherited", QA.SUPERVISOR_CODE)
        self.assertNotIn("docker", QA.WORKER_CODE.lower())
        self.assertNotIn("sqlite", QA.WORKER_CODE.lower())

    def test_bad_child_pid_denied(self) -> None:
        """Never permit PID 1 as a cleanup target."""
        (self.root / "child.pid").write_text("1")
        (self.root / "supervisor.ready").write_text("READY")
        parent = SimpleNamespace(poll=lambda: None)
        with self.assertRaisesRegex(RuntimeError, "INVALID_SYNTHETIC"):
            QA.wait_supervisor(self.root, parent)

    def test_supervisor_premature_exit_denied(self) -> None:
        """Missing handshake after parent exit must fail."""
        parent = SimpleNamespace(poll=lambda: 1)
        with self.assertRaisesRegex(RuntimeError, "EXITED_BEFORE"):
            QA.wait_supervisor(self.root, parent)

    def test_exact_marker_matching(self) -> None:
        """Only the desired state marker is recognized."""
        (self.root / "marker").write_text("RUNNING")
        QA.wait_marker(self.root, "RUNNING", QA.time.monotonic() + 1)

    def test_missing_marker_rejected(self) -> None:
        """A timed-out child handshake does not count as safe."""
        with self.assertRaisesRegex(RuntimeError, "MARKER_TIMEOUT"):
            QA.wait_marker(self.root, "RUNNING", 0)

    def test_cleanup_signals_unfinished_child(self) -> None:
        """Cleanup only signals the specific synthetic child PID."""
        with (
            patch.object(QA, "reap_supervisor") as parent,
            patch.object(QA.os, "kill") as child,
        ):
            QA.cleanup_case(self.root, SimpleNamespace(), 23456)
        parent.assert_called_once()
        child.assert_called_once_with(23456, signal.SIGTERM)

    def test_cleanup_does_not_kill_completed_child(self) -> None:
        """A completed synthetic child must not be signalled again."""
        (self.root / "marker").write_text("EXITED")
        with (
            patch.object(QA, "reap_supervisor"),
            patch.object(QA.os, "kill") as signalled,
        ):
            QA.cleanup_case(self.root, SimpleNamespace(), 23456)
        signalled.assert_not_called()

    def test_private_lock_path(self) -> None:
        """Lock metadata is contained inside a root-only private dir."""
        self.assertEqual(
            stat.S_IMODE((self.root / "maintenance.lock").stat().st_mode),
            0o600,
        )

    def test_inherited_case_classifies_held_B(self) -> None:
        """Mocked matching runtime must classify B EX as blocked."""
        (self.root / "marker").write_text("RUNNING")
        parent = SimpleNamespace(
            send_signal=lambda value: None, wait=lambda timeout: None
        )
        with (
            patch.object(QA, "spawn_supervisor", return_value=parent),
            patch.object(QA, "wait_supervisor", return_value=34567),
            patch.object(QA, "can_get_exclusive", side_effect=[False, False]),
            patch.object(QA, "wait_marker"),
            patch.object(QA, "wait_exclusive_release"),
            patch.object(QA, "cleanup_case"),
        ):
            evidence = QA.case(self.root, "inherited")
        self.assertEqual(
            evidence["maintenance_B_EX_while_writer_running"], "BLOCKED"
        )

    def test_parent_only_case_reports_gap(self) -> None:
        """Mocked parent death must reveal missing FD inheritance."""
        (self.root / "marker").write_text("RUNNING")
        parent = SimpleNamespace(
            send_signal=lambda value: None, wait=lambda timeout: None
        )
        with (
            patch.object(QA, "spawn_supervisor", return_value=parent),
            patch.object(QA, "wait_supervisor", return_value=34567),
            patch.object(QA, "can_get_exclusive", side_effect=[False, True]),
            patch.object(QA, "wait_marker"),
            patch.object(QA, "wait_exclusive_release"),
            patch.object(QA, "cleanup_case"),
        ):
            evidence = QA.case(self.root, "parent_only")
        self.assertEqual(
            evidence["maintenance_B_EX_while_writer_running"], "ALLOWED_GAP"
        )


if __name__ == "__main__":
    unittest.main()
