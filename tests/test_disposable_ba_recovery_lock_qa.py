"""Safe CLI and isolated fixture tests for B/A recovery QA."""

from __future__ import annotations

import contextlib
import fcntl
import importlib.util
import io
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/disposable-ba-recovery-lock-qa.py"
)
SPEC = importlib.util.spec_from_file_location("ba_recovery_qa", SCRIPT)
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)


class PrivateBALockRecoveryTests(unittest.TestCase):
    """Never open a production path or start a service in unit tests."""

    def setUp(self) -> None:
        """Create private SQLite and fake recovery source fixtures."""
        directory = tempfile.TemporaryDirectory(
            prefix="svt-ba-recovery-unit-",
            dir="/tmp",
        )
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.original = self.root / "original.sqlite3"
        self.candidate = self.root / "candidates"
        self.candidate.mkdir()
        with contextlib.closing(sqlite3.connect(self.original)) as db, db:
            db.execute("CREATE TABLE measurements(id INTEGER,voltage REAL)")
            db.execute("INSERT INTO measurements VALUES(1,225.5)")
        for filename in QA.EXPECTED:
            (self.candidate / filename).write_text("QA_FAKE_SOURCE\n")
        for attr, value in (
            ("ORIGINAL", self.original),
            ("CANDIDATE", self.candidate),
        ):
            replacement = patch.object(QA, attr, value)
            replacement.start()
            self.addCleanup(replacement.stop)

    def cli(self, *args: str) -> tuple[int, str]:
        """Run CLI with captured output and no background actions."""
        output = io.StringIO()
        with (
            patch.object(sys, "argv", [str(SCRIPT), *args]),
            contextlib.redirect_stdout(output),
        ):
            return QA.main(), output.getvalue()

    def test_default_inert(self) -> None:
        """No QA action without exact explicit opt-in."""
        with patch.object(QA, "check_guest", side_effect=AssertionError):
            code, message = self.cli()
        self.assertEqual(code, 0)
        self.assertIn("DRY-RUN", message)

    def test_dry_run_precedes_run_flag(self) -> None:
        """Dry-run wins over --run-disposable even with invalid state."""
        with patch.object(QA, "check_guest", side_effect=AssertionError):
            self.assertEqual(self.cli("--run-disposable", "--dry-run")[0], 0)

    def test_bad_confirmation_never_runs(self) -> None:
        """A generic 'yes' token cannot start even a private QA run."""
        with patch.object(QA, "check_guest", side_effect=AssertionError):
            code, message = self.cli("--run-disposable", "--confirm", "yes")
        self.assertEqual(code, 2)
        self.assertIn("FAIL", message)

    def test_help(self) -> None:
        """Both command-help flags do not require network or Docker."""
        for flag in ("-h", "--help"):
            with self.subTest(flag=flag):
                result = subprocess.run(
                    [sys.executable, str(SCRIPT), flag],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                self.assertEqual(result.returncode, 0)
                self.assertIn("--run-disposable", result.stdout)

    def test_explicit_json_output_denies_production(self) -> None:
        """Even explicit success never grants deployment authority."""
        with (
            patch.object(QA, "check_guest"),
            patch.object(
                QA,
                "exercise",
                side_effect=[{"case": "clean"}, {"case": "append"}],
            ),
        ):
            code, result = self.cli(
                "--run-disposable",
                "--confirm",
                QA.CONFIRM,
                "--jsonl",
            )
        self.assertEqual(code, 0)
        payload = json.loads(result)
        self.assertFalse(payload["production_modified"])
        self.assertFalse(payload["owner_maintenance_authorized"])
        self.assertFalse(payload["telegram_contacted"])
        self.assertEqual(len(payload["cases"]), 2)

    def test_guard_error_is_stable_exit_three(self) -> None:
        """An invalid QA environment always fails, never silently runs."""
        with patch.object(
            QA, "check_guest", side_effect=RuntimeError("WRONG_QA_HOST")
        ):
            code, message = self.cli(
                "--run-disposable", "--confirm", QA.CONFIRM
            )
        self.assertEqual(code, 3)
        self.assertIn("WRONG_QA_HOST", message)

    def test_invalid_scenario_rejected(self) -> None:
        """Only the two approved private recovery experiments exist."""
        with self.assertRaisesRegex(RuntimeError, "INVALID_QA_SCENARIO"):
            QA.exercise("production")

    def test_wrong_hostname_rejected(self) -> None:
        """Never run this test on LXC110 or any production environment."""
        with (
            patch.object(QA.os, "geteuid", return_value=0),
            patch.object(
                QA.os,
                "uname",
                return_value=SimpleNamespace(nodename="production"),
            ),
            self.assertRaisesRegex(RuntimeError, "NOT_DISPOSABLE_ROOT_GUEST"),
        ):
            QA.check_guest()

    def test_network_interface_rejected(self) -> None:
        """No additional network interface can be present."""
        with (
            patch.object(QA.os, "geteuid", return_value=0),
            patch.object(
                QA.os,
                "uname",
                return_value=SimpleNamespace(
                    nodename="shelly-docker-reboot-qa",
                ),
            ),
            patch.object(
                QA.socket,
                "if_nameindex",
                return_value=[(1, "lo"), (2, "eth0")],
            ),
            self.assertRaisesRegex(
                RuntimeError, "QA_GUEST_HAS_EXTERNAL_NETWORK"
            ),
        ):
            QA.check_guest()

    def test_missing_database_refused(self) -> None:
        """Never create an absent database by opening for recovery."""
        self.original.unlink()
        with (
            patch.object(QA.os, "geteuid", return_value=0),
            patch.object(
                QA.os,
                "uname",
                return_value=SimpleNamespace(
                    nodename="shelly-docker-reboot-qa",
                ),
            ),
            patch.object(
                QA.socket,
                "if_nameindex",
                return_value=[(1, "lo")],
            ),
            self.assertRaisesRegex(
                RuntimeError, "QA_HISTORY_MISSING_OR_LINKED"
            ),
        ):
            QA.check_guest()
        self.assertFalse(self.original.exists())

    def test_candidate_source_sha_is_pinned(self) -> None:
        """A different Bash recovery source cannot be executed."""
        with (
            patch.object(QA.os, "geteuid", return_value=0),
            patch.object(
                QA.os,
                "uname",
                return_value=SimpleNamespace(
                    nodename="shelly-docker-reboot-qa",
                ),
            ),
            patch.object(
                QA.socket,
                "if_nameindex",
                return_value=[(1, "lo")],
            ),
            self.assertRaisesRegex(RuntimeError, "QA_CANDIDATE_SHA_MISMATCH"),
        ):
            QA.check_guest()

    def test_environment_targets_private_root(self) -> None:
        """The child Bash receives only QA fixture path overrides."""
        result = QA.recovery_env(self.root, "append")
        self.assertEqual(result["SVT_ROOT"], str(self.root))
        self.assertEqual(result["QA_APPEND"], "1")
        self.assertIn(str(self.root), result["DATABASE_PATH"])
        self.assertEqual(result["QA_IMAGE"], QA.IMAGE)

    def test_fixture_snapshot_has_valid_checksum(self) -> None:
        """The snapshot contains only a private original SQL row."""
        with patch.object(QA.os, "chown"):
            rows = QA.make_fixture(self.root)
        self.assertEqual(rows, 1)
        self.assertTrue((self.root / "state/pending-update").exists())
        with contextlib.closing(
            sqlite3.connect(self.root / "data/current.sqlite3")
        ) as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM measurements").fetchone()[0],
                1,
            )

    def test_lock_probe_free_shared(self) -> None:
        """A private unheld file accepts an independent reader."""
        path = self.root / "maintenance.lock"
        path.touch(mode=0o600)
        self.assertEqual(QA.lock_probe(path, fcntl.LOCK_SH), "ACQUIRED")

    def test_lock_probe_exclusive_blocks_shared(self) -> None:
        """Updater EX prevents a concurrent supervisor SH request."""
        path = self.root / "maintenance.lock"
        path.touch(mode=0o600)
        with path.open("rb") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            self.assertEqual(QA.lock_probe(path, fcntl.LOCK_SH), "BLOCKED")

    def test_await_stop_marker_available(self) -> None:
        """The intercepted-stop stage is detected without sleeping."""
        (self.root / "stop-reached").write_text("STOP\n")
        QA.await_stop(self.root, SimpleNamespace(poll=lambda: None))

    def test_await_stop_early_process_exit_blocks(self) -> None:
        """No fabricated PASS when Bash exits before its stop stage."""
        process = SimpleNamespace(poll=lambda: 1)
        with self.assertRaisesRegex(
            RuntimeError, "BASH_RECOVERY_EXITED_BEFORE_STOP"
        ):
            QA.await_stop(self.root, process)

    def test_clean_result_removes_synthetic_pending(self) -> None:
        """Equal-history synthetic recovery restores old metadata."""
        data = self.root / "data"
        state = self.root / "state"
        public = self.root / "public"
        for directory in (data, state, public):
            directory.mkdir(exist_ok=True)
        with (
            contextlib.closing(
                sqlite3.connect(data / "current.sqlite3")
            ) as db,
            db,
        ):
            db.execute("CREATE TABLE measurements(id INTEGER,voltage REAL)")
            db.execute("INSERT INTO measurements VALUES(1,225.5)")
        (state / "override.yaml").write_text("qa: previous\n")
        (public / "deployed.json").write_text("{}")
        result = QA.check_result(self.root, 1, "clean", 0)
        self.assertEqual(result["rows_after"], 1)
        self.assertFalse(result["pending"])

    def test_append_result_keeps_pending(self) -> None:
        """New committed synthetic rows must never be rolled back."""
        data = self.root / "data"
        state = self.root / "state"
        data.mkdir(exist_ok=True)
        state.mkdir(exist_ok=True)
        with (
            contextlib.closing(
                sqlite3.connect(data / "current.sqlite3")
            ) as db,
            db,
        ):
            db.execute("CREATE TABLE measurements(id INTEGER,voltage REAL)")
            db.execute("INSERT INTO measurements VALUES(1,225.5)")
            db.execute("INSERT INTO measurements VALUES(2,221.7)")
        (state / "pending-update").write_text("synthetic\n")
        (state / "override.yaml").write_text("qa: candidate\n")
        result = QA.check_result(self.root, 1, "append", 1)
        self.assertEqual(result["rows_after"], 2)
        self.assertTrue(result["pending"])

    def test_wrong_rollback_exit_not_accepted(self) -> None:
        """A failure is never mistaken for successful clean recovery."""
        (self.root / "data").mkdir()
        (self.root / "state").mkdir()
        with (
            contextlib.closing(
                sqlite3.connect(self.root / "data/current.sqlite3")
            ) as db,
            db,
        ):
            db.execute("CREATE TABLE measurements(id INTEGER,voltage REAL)")
            db.execute("INSERT INTO measurements VALUES(1,225.5)")
        with self.assertRaises(AssertionError):
            QA.check_result(self.root, 1, "clean", 1)


if __name__ == "__main__":
    unittest.main()
