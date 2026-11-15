"""Unit tests for private admission G, writer B/W and updater A handoff."""

from __future__ import annotations

import contextlib
import fcntl
import importlib.util
import io
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "disposable-admission-gate-handoff-qa.py"
SPEC = importlib.util.spec_from_file_location("qa_gate_handoff", SCRIPT)
QA = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = QA
SPEC.loader.exec_module(QA)


class AdmissionHandoffTests(unittest.TestCase):
    """Never inspect production services from ordinary unit tests."""

    def setUp(self) -> None:
        """Prepare synthetic independent lock inodes in disposable scratch."""
        temp = tempfile.TemporaryDirectory(prefix="g-gate-unit-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.baseline = QA.prepare(self.root)

    def invoke(self, *args: str) -> int:
        """Capture a CLI result and reject unexpected offline host probing."""
        with (
            patch.object(sys, "argv", ["qa", *args]),
            patch.object(QA, "offline_guard", side_effect=AssertionError),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            return QA.main()

    def test_default_cli_is_inert(self) -> None:
        """Without explicit flags no writer or Docker operation may run."""
        self.assertEqual(self.invoke(), 0)

    def test_generic_yes_rejected(self) -> None:
        """A generic confirmation is insufficient to start any QA work."""
        self.assertEqual(
            self.invoke("--run-disposable", "--confirm", "yes"), 2
        )

    def test_fixed_qa_guest_guard(self) -> None:
        """Only the adjacent networkless LXC114 preflight is accepted."""
        self.assertEqual(QA.GUARD.parent, SCRIPT.parent)
        self.assertTrue(QA.GUARD.is_file())

    def test_synthetic_child_code_compiles(self) -> None:
        """Child code uses only Python, file locks and private marker files."""
        compile(QA.CHILD, "qa-admission-child", "exec")
        self.assertIn('root / "G"', QA.CHILD)
        self.assertIn('root / "B"', QA.CHILD)
        self.assertIn('root / "W"', QA.CHILD)
        self.assertNotIn("docker", QA.CHILD.lower())
        self.assertNotIn("sqlite", QA.CHILD.lower())

    def test_G_B_W_A_are_distinct_private_inodes(self) -> None:
        """Root-private mode0600 inodes must not alias one another."""
        self.assertEqual(len(self.baseline), 4)
        self.assertEqual(len(set(self.baseline.values())), 4)
        for name in self.baseline:
            item = self.root / name
            self.assertEqual(stat.S_IMODE(item.stat().st_mode), 0o600)

    def test_B_shared_denies_maintenance_exclusive(self) -> None:
        """A live writer's B SH lock prevents B EX."""
        with (self.root / "B").open("rb") as holder:
            fcntl.flock(holder, fcntl.LOCK_SH | fcntl.LOCK_NB)
            self.assertFalse(QA.can_lock(self.root, "B", fcntl.LOCK_EX))

    def test_G_exclusive_denies_admission_shared(self) -> None:
        """Under maintenance G EX, a new G SH attempt fails."""
        with (self.root / "G").open("rb") as holder:
            fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertFalse(QA.can_lock(self.root, "G", fcntl.LOCK_SH))

    def test_W_exclusive_excludes_second_writer(self) -> None:
        """A running writer's W EX forbids another W EX owner."""
        with (self.root / "W").open("rb") as holder:
            fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertFalse(QA.can_lock(self.root, "W", fcntl.LOCK_EX))

    def test_lock_identity_replacement_denied(self) -> None:
        """An inode replacement prevents successful maintenance handoff."""
        old = self.root / "B"
        substitute = self.root / "B.new"
        substitute.touch(mode=0o600)
        with old.open("rb"):
            os.replace(substitute, old)
            with self.assertRaisesRegex(RuntimeError, "INODE_REPLACED"):
                QA.verify_inodes(self.root, self.baseline)

    def test_valid_lock_identities_preserved(self) -> None:
        """A clean private fixture passes an exact device/inode comparison."""
        QA.verify_inodes(self.root, self.baseline)

    def test_unknown_worker_handshake_denied(self) -> None:
        """Never interpret a wrong process state as successful admission."""
        (self.root / "invented.status").write_text("WRONG")
        with self.assertRaisesRegex(RuntimeError, "UNEXPECTED"):
            QA.await_marker(self.root, "invented", "RUNNING")

    def test_worker_creation_is_python_only(self) -> None:
        """No unit test starts shell commands, Docker or network processes."""
        fake = SimpleNamespace()
        with patch.object(QA.subprocess, "Popen", return_value=fake) as spawn:
            self.assertIs(QA.worker(self.root, "test"), fake)
        args = spawn.call_args.args[0]
        self.assertEqual(args[:3], [sys.executable, "-c", QA.CHILD])
        self.assertEqual(args[-1], "test")

    def test_stopping_worker_targets_only_its_marker(self) -> None:
        """A synthetic worker stop requests one exact known PID target."""
        fake = SimpleNamespace(wait=lambda timeout: 0)
        QA.stop_worker(self.root, "invented", fake)
        self.assertTrue((self.root / "invented.stop").is_file())

    def test_stop_nonzero_is_failure(self) -> None:
        """A failed synthetic exit is never classified as safe quiescence."""
        fake = SimpleNamespace(wait=lambda timeout: 7)
        with self.assertRaisesRegex(RuntimeError, "FAILED_TO_STOP"):
            QA.stop_worker(self.root, "invented", fake)

    def test_reap_owned_child_only(self) -> None:
        """Cleanup must signal only the process object it started."""
        calls: list[str] = []
        fake = SimpleNamespace(
            poll=lambda: None,
            terminate=lambda: calls.append("terminate"),
            wait=lambda timeout: calls.append("wait"),
        )
        QA.reap([fake])
        self.assertEqual(calls, ["terminate", "wait"])

    def test_actual_synthetic_handoff_passes(self) -> None:
        """Real isolated Unix role handoff must preserve all four inodes."""
        result = QA.run_suite()
        self.assertEqual(result["result"], "PASS_G_EX_STOP_B_EX_A_EX_HANDOFF")
        self.assertTrue(result["G_EX_while_incumbent_B_SH_active"])
        self.assertTrue(result["second_writer_blocked_at_admission"])
        self.assertTrue(result["all_G_B_W_A_inodes_stable"])
        self.assertFalse(result["docker_used"])
        self.assertFalse(result["sqlite_used"])
        self.assertFalse(result["production_modified"])


if __name__ == "__main__":
    unittest.main()
