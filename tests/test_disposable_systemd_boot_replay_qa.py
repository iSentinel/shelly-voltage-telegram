"""Validate boot replay QA cannot target production or change real services."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/disposable-systemd-boot-replay-qa.py"
)
SPEC = importlib.util.spec_from_file_location("svt_boot_replay", SCRIPT)
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)
UNIT = "svt-qa-boot-replay-abcdef123456"


class BootReplayUnitTests(unittest.TestCase):
    """All unit test inputs are temporary synthetic SQLite fixtures."""

    def setUp(self) -> None:
        """Allocate a private scratch directory, without accessing Docker."""
        temporary = tempfile.TemporaryDirectory(
            prefix="svt-replay-unit-",
            dir="/tmp",
        )
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "case"
        QA.create_fixture(self.root, "clean")

    def invoke(self, *args: str) -> int:
        """Run the CLI in a mock-protected, output-suppressed context."""
        with (
            patch.object(sys, "argv", [str(SCRIPT), *args]),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            return QA.main()

    def test_default_dry_run_invokes_no_host_actions(self) -> None:
        """Neither default nor explicit dry-run can start systemd."""
        with (
            patch.object(QA, "run", side_effect=AssertionError),
            patch.object(QA, "execute", side_effect=AssertionError),
        ):
            self.assertEqual(self.invoke(), 0)
            self.assertEqual(self.invoke("--dry-run"), 0)

    def test_missing_explicit_confirmation_is_rejected(self) -> None:
        """Arbitrary strings cannot start disposable integration."""
        with patch.object(QA, "execute", side_effect=AssertionError):
            self.assertEqual(self.invoke("--run-disposable"), 2)
            self.assertEqual(
                self.invoke("--run-disposable", "--confirm", "yes"), 2
            )

    def test_exact_confirm_reaches_runner(self) -> None:
        """Only the exact QA-only phrase can invoke systemd integration."""
        with patch.object(QA, "execute") as execute:
            self.assertEqual(
                self.invoke(
                    "--run-disposable",
                    "--confirm",
                    QA.CONFIRM,
                ),
                0,
            )
            execute.assert_called_once_with()

    def test_only_generated_service_names_allowed(self) -> None:
        """A real Compose or arbitrary updater unit cannot be selected."""
        for name in (
            "shelly-voltage-compose",
            "shelly-voltage-bot",
            "svt-qa-boot-replay-123",
            UNIT + ";rm",
        ):
            with (
                self.subTest(name=name),
                self.assertRaises(QA.Blocked),
            ):
                QA.service_command(name, self.root)
        self.assertEqual(
            QA.service_name().split("-")[:4], ["svt", "qa", "boot", "replay"]
        )

    def test_service_command_targets_only_scratch_inputs(self) -> None:
        """No live database, source or container path may enter the argv."""
        cmd = QA.service_command(UNIT, self.root)
        self.assertEqual(cmd[0:3], ["systemd-run", "--wait", "--collect"])
        prestart = next(
            x for x in cmd if x.startswith("--property=ExecStartPre")
        )
        self.assertIn(str(self.root / "history.sqlite3"), prestart)
        self.assertIn("--runtime-fixture", prestart)
        self.assertNotIn("--live-readonly", prestart)
        self.assertNotIn("/opt/shelly-voltage/", prestart)
        self.assertNotIn("docker", str(cmd))
        self.assertEqual(
            cmd[-2:], ["/usr/bin/touch", str(self.root / "main-started")]
        )

    def test_pending_case_uses_0600_journal(self) -> None:
        """A fake pending marker must be preserved in QA."""
        root = self.base / "pending"
        QA.create_fixture(root, "pending")
        marker = root / "state/pending-update"
        self.assertEqual(marker.stat().st_mode & 0o777, 0o600)
        self.assertTrue(marker.is_file())

    def test_pending_wal_committed_sample_is_present(self) -> None:
        """The test creates a real additional SQLite sample."""
        root = self.base / "wal"
        QA.create_fixture(root, "pending-wal")
        with closing(sqlite3.connect(root / "history.sqlite3")) as db:
            count = db.execute("SELECT count(*) FROM measurements").fetchone()[
                0
            ]
        self.assertEqual(count, 121)

    def test_clean_case_contains_private_fake_image_pin(self) -> None:
        """Pin and receipt must be synthetic, never real production refs."""
        pin = self.root / "state/override.yaml"
        receipt = self.root / "state/deployed.json"
        self.assertIn(QA.IMAGE, pin.read_text())
        self.assertEqual(json.loads(receipt.read_text())["digest"], QA.DIGEST)
        self.assertEqual(pin.stat().st_mode & 0o777, 0o600)

    def test_missing_pin_case_deletes_only_fake_pin(self) -> None:
        """The missing-pin test must not touch actual updater state."""
        root = self.base / "missing"
        QA.create_fixture(root, "missing-pin")
        self.assertFalse((root / "state/override.yaml").exists())
        self.assertTrue((root / "state/deployed.json").exists())

    def test_corrupt_database_case_uses_fake_data(self) -> None:
        """Injected corruption is restricted to a scratch SQLite file."""
        root = self.base / "corrupt"
        QA.create_fixture(root, "corrupt-db")
        self.assertEqual(
            (root / "history.sqlite3").read_bytes(),
            b"intentionally invalid SQLite fixture",
        )

    def test_invalid_case_cannot_run(self) -> None:
        """Only the hardcoded QA scenario list is permitted."""
        with self.assertRaises(QA.Blocked):
            QA.create_fixture(self.base / "invalid", "production")

    def test_fingerprint_stable_with_no_writes(self) -> None:
        """The scratch history remains unchanged across read-only checks."""
        first = QA.data_fingerprint(self.root)
        second = QA.data_fingerprint(self.root)
        self.assertEqual(first, second)

    def test_fingerprint_detects_mutation(self) -> None:
        """A changed synthetic measurement invalidates boot acceptance."""
        first = QA.data_fingerprint(self.root)
        with closing(sqlite3.connect(self.root / "history.sqlite3")) as db:
            db.execute("UPDATE measurements SET voltage=123")
            db.commit()
        self.assertNotEqual(first, QA.data_fingerprint(self.root))

    def test_cleanup_only_generated_service_name(self) -> None:
        """No production systemctl stop or reset is reachable."""
        with patch.object(QA, "run", return_value="") as mocked:
            QA.cleanup_service(UNIT)
        args = [entry.args[0] for entry in mocked.call_args_list]
        self.assertEqual(
            args,
            [
                ["systemctl", "stop", UNIT + ".service"],
                ["systemctl", "reset-failed", UNIT + ".service"],
            ],
        )
        with self.assertRaises(QA.Blocked):
            QA.cleanup_service("shelly-voltage-compose")

    def test_journal_only_generated_unit(self) -> None:
        """Journal reads are scoped to a random QA unit name."""
        with patch.object(QA, "run", return_value="PASS") as runner:
            self.assertEqual(QA.journal_reason(UNIT), "PASS")
        self.assertIn(UNIT + ".service", runner.call_args.args[0])
        with self.assertRaises(QA.Blocked):
            QA.journal_reason("shelly-voltage-compose")

    def test_clean_execstart_requires_expected_main_marker(self) -> None:
        """A successful return code alone is not enough for QA PASS."""
        with self.assertRaises(QA.Blocked):
            QA.validate_case("clean", self.root, QA.REASONS["clean"], True)
        (self.root / "main-started").touch()
        QA.validate_case("clean", self.root, QA.REASONS["clean"], True)

    def test_blocked_case_requires_missing_execstart(self) -> None:
        """A blocked prestart must never permit the main service."""
        QA.validate_case(
            "source-mismatch",
            self.root,
            QA.REASONS["source-mismatch"],
            False,
        )
        (self.root / "main-started").touch()
        with self.assertRaises(QA.Blocked):
            QA.validate_case(
                "source-mismatch",
                self.root,
                QA.REASONS["source-mismatch"],
                False,
            )

    def test_wrong_journal_reason_cannot_pass(self) -> None:
        """Other systemd failures cannot masquerade as expected denial."""
        with self.assertRaises(QA.Blocked):
            QA.validate_case("missing-pin", self.root, "unrelated", False)

    def test_pending_marker_removal_fails_acceptance(self) -> None:
        """A refused boot must keep the diagnostic journal."""
        root = self.base / "pending-reason"
        QA.create_fixture(root, "pending")
        (root / "state/pending-update").unlink()
        with self.assertRaises(QA.Blocked):
            QA.validate_case(
                "pending",
                root,
                QA.REASONS["pending"],
                False,
            )

    def test_failing_systemd_command_reports_failure(self) -> None:
        """A failed systemd program cannot be interpreted as success."""
        result = subprocess.CompletedProcess(
            args=["systemd-run"],
            returncode=1,
            stdout="",
            stderr="",
        )
        with (
            patch.object(subprocess, "run", return_value=result),
            self.assertRaises(QA.Blocked),
        ):
            QA.run(["systemd-run"])

    def test_execute_runs_each_bounded_case(self) -> None:
        """Integration cannot silently skip any negative-control case."""
        with (
            patch.object(QA, "prerequisites"),
            patch.object(QA, "run_case") as runner,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            QA.execute()
        self.assertEqual(
            [call.args[1] for call in runner.call_args_list],
            list(QA.CASES),
        )

    def test_help_is_available(self) -> None:
        """Expose CLI usage without requesting host privileges."""
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "-h"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("--run-disposable", result.stdout)


if __name__ == "__main__":
    unittest.main()
