"""Regression tests for the isolated final local WAL cutover sequence."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/disposable-quiesced-local-cutover-qa.py"
SPEC = importlib.util.spec_from_file_location(
    "qa_quiesced_local_cutover", SCRIPT
)
QA = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = QA
SPEC.loader.exec_module(QA)


class LocalCutoverTests(unittest.TestCase):
    """Only invented SQLite and cooperative test writers are allowed."""

    def setUp(self) -> None:
        """Create a private temporary root that cannot touch production."""
        temp = tempfile.TemporaryDirectory(prefix="qa-cutover-unit-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.base = QA.load_qa()

    def invoke(self, *args: str) -> int:
        """No actual guest guard call without exact explicit integration."""
        with (
            patch.object(sys, "argv", ["qa", *args]),
            patch.object(QA, "load_qa", side_effect=AssertionError),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            return QA.main()

    def test_default_dry_run_is_inert(self) -> None:
        """Unadorned invocation never starts a writer or SQLite."""
        self.assertEqual(self.invoke(), 0)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_untrusted_confirmation_refused(self) -> None:
        """A generic confirmation does not authorize offline QA."""
        self.assertEqual(
            self.invoke("--run-disposable", "--confirm", "yes"), 2
        )

    def test_fixed_sibling_backup_guard(self) -> None:
        """The backup implementation comes from a fixed sibling QA file."""
        self.assertEqual(QA.BASE.parent, SCRIPT.parent)
        self.assertTrue(QA.BASE.is_file())

    def test_live_writer_refused_before_stop(self) -> None:
        """The final backup must never precede cooperative shutdown."""
        active = threading.Event()
        worker = threading.Thread(target=lambda: None)
        with self.assertRaisesRegex(RuntimeError, "WRITER_NOT_QUIESCENT"):
            QA.require_quiesced(active, worker, [])

    def test_stop_flag_without_exit_refused(self) -> None:
        """A signalled but still running writer cannot define cutover."""
        stop = threading.Event()
        stop.set()
        worker = threading.Thread(target=lambda: None)
        with (
            patch.object(worker, "is_alive", return_value=True),
            self.assertRaisesRegex(RuntimeError, "NOT_QUIESCENT"),
        ):
            QA.require_quiesced(stop, worker, [])

    def test_writer_error_refused_even_if_stopped(self) -> None:
        """A SQLite failure during stop invalidates backup acceptance."""
        stop = threading.Event()
        stop.set()
        worker = threading.Thread(target=lambda: None)
        with self.assertRaisesRegex(RuntimeError, "NOT_QUIESCENT"):
            QA.require_quiesced(stop, worker, ["OperationalError"])

    def test_unstarted_worker_can_be_explicitly_quiesced(self) -> None:
        """An unstarted test writer may be marked stopped, not active."""
        stop = threading.Event()
        worker = threading.Thread(target=lambda: None)
        QA.stop_writer(stop, worker, [])
        self.assertTrue(stop.is_set())

    def test_no_fake_cutover_before_new_rows(self) -> None:
        """An untouched 420-row source does not prove writer acceptance."""
        with (
            contextlib.closing(self.base.make_source(self.root)) as db,
            self.assertRaisesRegex(RuntimeError, "NEVER_COMMITTED"),
        ):
            QA.backup_after_stop(self.root, db, self.base)

    def test_two_snapshots_after_join_match_same_boundary(self) -> None:
        """A stopped writer's final source has identical restore states."""
        result = QA.exercise(self.root, self.base)
        self.assertEqual(result["status"], "PASS_QUIESCED_LOCAL_WAL_CUTOVER")
        self.assertGreater(result["rows_at_cutover"], 420)
        self.assertEqual(result["two_local_copies"], 2)
        self.assertTrue(result["both_restores_match_final_boundary"])

    def test_cutover_stays_single_media_no_go(self) -> None:
        """A good synthetic local rollback is not Owner Approval."""
        result = QA.exercise(self.root, self.base)
        self.assertEqual(result["release_decision"], "NO_GO")
        self.assertTrue(result["one_nvme_only"])
        self.assertFalse(result["every_real_production_start_owner_frozen"])

    def test_snapshot_integrity_after_quiescence(self) -> None:
        """Both restored standalone SQLite copies share the same rows."""
        QA.exercise(self.root, self.base)
        first = self.root / "cutover-A.sqlite3"
        second = self.root / "cutover-B.sqlite3"
        import sqlite3

        with (
            contextlib.closing(sqlite3.connect(first)) as a,
            contextlib.closing(sqlite3.connect(second)) as b,
        ):
            self.assertEqual(
                self.base.sqlite_state(a), self.base.sqlite_state(b)
            )

    def test_no_background_thread_survives(self) -> None:
        """Cooperative shutdown must join the exact owned test worker."""
        before = set(threading.enumerate())
        QA.exercise(self.root, self.base)
        self.assertEqual(before, set(threading.enumerate()))

    def test_main_requires_guard_before_integration(self) -> None:
        """Host isolation must be checked before any source DB is opened."""
        events: list[str] = []

        class Stub:
            """Check only the call sequence of the exact QA guard."""

            def guest_guard(self) -> None:
                """Record guard invocation without external host access."""
                events.append("guard")

        with (
            patch.object(
                sys,
                "argv",
                ["qa", "--run-disposable", "--confirm", QA.CONSENT],
            ),
            patch.object(QA, "load_qa", return_value=Stub()),
            patch.object(
                QA,
                "exercise",
                side_effect=lambda *_: events.append("run")
                or {"release_decision": "NO_GO"},
            ),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(QA.main(), 0)
        self.assertEqual(events, ["guard", "run"])

    def test_error_never_returns_release_success(self) -> None:
        """Any SQLite error must generate blocked status, not a release GO."""

        class Stub:
            """Offline guard stub for a failed synthetic cutover."""

            def guest_guard(self) -> None:
                """Do not inspect host or production."""
                return None

        with (
            patch.object(
                sys,
                "argv",
                ["qa", "--run-disposable", "--confirm", QA.CONSENT],
            ),
            patch.object(QA, "load_qa", return_value=Stub()),
            patch.object(QA, "exercise", side_effect=RuntimeError("invented")),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(QA.main(), 3)
        self.assertIn("QA_BLOCKED", output.getvalue())


if __name__ == "__main__":
    unittest.main()
