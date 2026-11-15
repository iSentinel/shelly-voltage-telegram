"""Default-inert tests for isolated shared-B and exclusive W admission."""

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
    / "scripts/disposable-single-writer-admission-qa.py"
)
SPEC = importlib.util.spec_from_file_location("qa_writer_admit", SCRIPT)
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)


class SingleWriterAdmissionTests(unittest.TestCase):
    """No unit test starts a real writer or accesses Docker."""

    def setUp(self) -> None:
        """Make a root-only synthetic B and W inode fixture."""
        directory = tempfile.TemporaryDirectory(prefix="admit-unit-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.folder = self.root / "locks"
        QA.prepare(self.folder)

    def invoke(self, *args: str) -> int:
        """Run the default-inert command line with output hidden."""
        with (
            patch.object(sys, "argv", ["qa", *args]),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            return QA.main()

    def test_default_inert(self) -> None:
        """No consent means no filesystem or external guest access."""
        with patch.object(QA, "load_guard", side_effect=AssertionError):
            self.assertEqual(self.invoke(), 0)

    def test_unrecognized_consent_denied(self) -> None:
        """A generic yes never starts real flock processes."""
        with patch.object(QA, "load_guard", side_effect=AssertionError):
            self.assertEqual(
                self.invoke("--run-disposable", "--confirm", "yes"), 2
            )

    def test_fixed_offline_guard_location(self) -> None:
        """Only the fixed sibling QA safety module is permitted."""
        self.assertEqual(QA.GUARD_SOURCE.parent, SCRIPT.parent)
        self.assertTrue(QA.GUARD_SOURCE.is_file())

    def test_distinct_private_B_and_W(self) -> None:
        """B and W locks must be separate owner-private inodes."""
        B = self.folder / "maintenance.lock"
        W = self.folder / "writer.lock"
        self.assertNotEqual(B.stat().st_ino, W.stat().st_ino)
        self.assertEqual(stat.S_IMODE(B.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(W.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.folder.stat().st_mode), 0o700)

    def test_two_B_shared_fds_succeed(self) -> None:
        """Actual flock admits two independent shared fd owners."""
        with (
            (self.folder / "maintenance.lock").open("rb") as first,
            (self.folder / "maintenance.lock").open("rb") as second,
        ):
            fcntl.flock(first, fcntl.LOCK_SH | fcntl.LOCK_NB)
            fcntl.flock(second, fcntl.LOCK_SH | fcntl.LOCK_NB)
            self.assertFalse(
                QA.lock_try_exclusive(self.folder / "maintenance.lock")
            )

    def test_W_exclusive_prevents_second_holder(self) -> None:
        """Actual separate writer lock excludes competing writer."""
        with (self.folder / "writer.lock").open("rb") as first:
            fcntl.flock(first, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertFalse(
                QA.lock_try_exclusive(self.folder / "writer.lock")
            )

    def test_shared_only_command(self) -> None:
        """B SH alone has no writer exclusivity mechanism."""
        args = QA.command(self.folder, False)
        self.assertIn("--shared", args)
        self.assertIn("--no-fork", args)
        self.assertNotIn("writer.lock", " ".join(args))
        self.assertEqual(args[-2:], ["/usr/bin/sleep", "3"])

    def test_two_lock_command_order(self) -> None:
        """Synthetic writer requires B SH before W EX with no fork."""
        args = QA.command(self.folder, True)
        self.assertLess(args.index("--shared"), args.index("--exclusive"))
        self.assertIn(str(self.folder / "maintenance.lock"), args)
        self.assertIn(str(self.folder / "writer.lock"), args)
        self.assertEqual(args.count("--no-fork"), 2)

    def test_spawn_never_calls_docker(self) -> None:
        """Sandbox command runs bounded sleep, not real Docker."""
        with patch.object(QA.subprocess, "Popen") as mocked:
            QA.spawn(self.folder, True)
        args = mocked.call_args.args[0]
        self.assertEqual(args[-2:], ["/usr/bin/sleep", "3"])
        self.assertNotIn("docker", " ".join(args))
        self.assertNotIn("sqlite", " ".join(args))

    def test_exited_worker_cannot_claim_lock(self) -> None:
        """A dead subprocess cannot pass admission verification."""
        child = SimpleNamespace(poll=lambda: 1)
        with self.assertRaisesRegex(RuntimeError, "EXITED_BEFORE_LOCK"):
            QA.wait_lock_claim(self.folder, child, False)

    def test_owned_subprocess_is_reaped(self) -> None:
        """Cleanup targets its exact no-fork synthetic child."""
        child = SimpleNamespace(
            poll=lambda: None,
            terminate=lambda: None,
            wait=lambda timeout: 0,
        )
        QA.finish(child)

    def test_suite_never_claims_real_storage(self) -> None:
        """Result must explicitly identify absence of real Docker/SQLite."""
        first = {"case": "B_SH_ONLY"}
        second = {"case": "B_SH_THEN_W_EX"}
        with (
            patch.object(QA, "shared_only_case", return_value=first),
            patch.object(QA, "exclusive_writer_case", return_value=second),
        ):
            result = QA.run_suite()
        self.assertEqual(len(result["cases"]), 2)
        self.assertFalse(result["docker_used"])
        self.assertFalse(result["sqlite_used"])
        self.assertFalse(result["production_modified"])


if __name__ == "__main__":
    unittest.main()
