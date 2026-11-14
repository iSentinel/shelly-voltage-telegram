"""Unit-test synthetic systemd B/A QA without touching real services."""

from __future__ import annotations

import contextlib
import fcntl
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/disposable-systemd-ba-start-reload-qa.py"
)
SPEC = importlib.util.spec_from_file_location("isolated_systemd_ba", SCRIPT)
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)


def result(code: int = 0, stdout: str = "") -> SimpleNamespace:
    """Provide one simulated systemctl/Docker command response."""
    return SimpleNamespace(returncode=code, stdout=stdout, stderr="")


class SystemdBALockQATests(unittest.TestCase):
    """Use mock systemctl calls and private temporary lockfiles only."""

    def setUp(self) -> None:
        """Prepare private B/A files with no systemd installation."""
        tmp = tempfile.TemporaryDirectory(
            prefix="svt-systemd-ba-unit-", dir="/tmp"
        )
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "maintenance.lock").touch(mode=0o600)
        (self.root / "updater.lock").touch(mode=0o600)

    def cli(self, *flags: str) -> tuple[int, str]:
        """Capture the standalone CLI without subprocess execution."""
        stdout = io.StringIO()
        with (
            patch.object(sys, "argv", [str(SCRIPT), *flags]),
            contextlib.redirect_stdout(stdout),
        ):
            return QA.main(), stdout.getvalue()

    def test_default_inert(self) -> None:
        """Default command must never inspect the QA guest."""
        with patch.object(QA, "check_guest", side_effect=AssertionError):
            code, output = self.cli()
        self.assertEqual(code, 0)
        self.assertIn("DRY-RUN", output)

    def test_dry_run_wins_over_execution_flag(self) -> None:
        """No side effects when explicitly requesting dry-run."""
        with patch.object(QA, "check_guest", side_effect=AssertionError):
            self.assertEqual(self.cli("--run-disposable", "--dry-run")[0], 0)

    def test_invalid_confirmation(self) -> None:
        """A generic yes token cannot authorize disposable systemd."""
        with patch.object(QA, "check_guest", side_effect=AssertionError):
            code, _ = self.cli("--run-disposable", "--confirm", "yes")
        self.assertEqual(code, 2)

    def test_help(self) -> None:
        """-h/--help display without reading any service."""
        for option in ("-h", "--help"):
            with self.subTest(option=option):
                with (
                    patch.object(sys, "argv", [str(SCRIPT), option]),
                    contextlib.redirect_stdout(io.StringIO()),
                    self.assertRaises(SystemExit) as raised,
                ):
                    QA.main()
                self.assertEqual(raised.exception.code, 0)

    def test_guest_wrong_hostname_rejected(self) -> None:
        """Never operate on the production LXC or another host."""
        with (
            patch.object(QA.os, "geteuid", return_value=0),
            patch.object(
                QA.os,
                "uname",
                return_value=SimpleNamespace(nodename="production"),
            ),
            self.assertRaisesRegex(RuntimeError, "DISPOSABLE_LXC114_ONLY"),
        ):
            QA.check_guest()

    def test_guest_external_network_rejected(self) -> None:
        """Any physical/nonloopback NIC makes QA execution invalid."""
        with (
            patch.object(QA.os, "geteuid", return_value=0),
            patch.object(
                QA.os,
                "uname",
                return_value=SimpleNamespace(
                    nodename="shelly-docker-reboot-qa"
                ),
            ),
            patch.object(
                QA.socket,
                "if_nameindex",
                return_value=[(1, "lo"), (2, "eth0")],
            ),
            self.assertRaisesRegex(RuntimeError, "QA_EXTERNAL_NETWORK"),
        ):
            QA.check_guest()

    def test_qa_owner_enabled_rejected(self) -> None:
        """Do not touch a guest with a running or enabled QA owner."""
        with (
            patch.object(QA.os, "geteuid", return_value=0),
            patch.object(
                QA.os,
                "uname",
                return_value=SimpleNamespace(
                    nodename="shelly-docker-reboot-qa"
                ),
            ),
            patch.object(QA.socket, "if_nameindex", return_value=[(1, "lo")]),
            patch.object(QA, "command", return_value=result(0, "enabled\n")),
            self.assertRaisesRegex(RuntimeError, "QA_ORIGINAL_OWNER_ENABLED"),
        ):
            QA.check_guest()

    def test_writer_running_rejected(self) -> None:
        """A running QA writer invalidates the exact fixture."""
        with (
            patch.object(QA.os, "geteuid", return_value=0),
            patch.object(
                QA.os,
                "uname",
                return_value=SimpleNamespace(
                    nodename="shelly-docker-reboot-qa"
                ),
            ),
            patch.object(QA.socket, "if_nameindex", return_value=[(1, "lo")]),
            patch.object(
                QA,
                "command",
                side_effect=[
                    result(0, "disabled\n"),
                    result(0, "disabled\n"),
                    result(0, "true|no|none\n"),
                ],
            ),
            self.assertRaisesRegex(
                RuntimeError, "QA_ORIGINAL_WRITER_NOT_SAFE"
            ),
        ):
            QA.check_guest()

    def test_prepare_unit_is_private_and_synthetic(self) -> None:
        """Install only a random test target and never a real unit name."""
        for filename in ("maintenance.lock", "updater.lock"):
            (self.root / filename).unlink()
        with patch.object(QA, "UNIT_ROOT", self.root):
            unit = QA.prepare_unit(
                self.root, "svt-qa-ba-owner-fixture.service"
            )
        self.assertTrue(unit.is_file())
        content = unit.read_text()
        self.assertIn("ExecStartPre=/usr/bin/flock -w 2", content)
        self.assertIn("ExecReload=/usr/bin/python3", content)
        self.assertNotIn("shelly-voltage-compose.service", content)

    def test_assert_action_success(self) -> None:
        """An expected success must be accepted without side effects."""
        with patch.object(QA, "systemctl", return_value=result(0)):
            self.assertIsNone(QA.assert_action("qa.service", "start", True))

    def test_assert_action_rejects_unexpected_success(self) -> None:
        """A blocked test that accidentally succeeds is FAIL."""
        with (
            patch.object(QA, "systemctl", return_value=result(0)),
            self.assertRaisesRegex(RuntimeError, "QA_RELOAD_RESULT_MISMATCH"),
        ):
            QA.assert_action("qa.service", "reload", False)

    def test_assert_action_rejects_unexpected_failure(self) -> None:
        """A required systemd start cannot be reported PASS on failure."""
        with (
            patch.object(QA, "systemctl", return_value=result(1)),
            self.assertRaisesRegex(RuntimeError, "QA_START_RESULT_MISMATCH"),
        ):
            QA.assert_action("qa.service", "start", True)

    def test_read_events_missing(self) -> None:
        """No events are observed before synthetic test starts."""
        self.assertEqual(QA.read_events(self.root), [])

    def test_check_start_covers_blocked_and_allowed(self) -> None:
        """An actual test sequence cannot omit B EX or B SH cases."""
        results = {}
        with (
            patch.object(QA, "assert_action") as action,
            patch.object(QA, "read_events", side_effect=[[], ["start"]]),
        ):
            QA.check_start(self.root, "qa.service", results)
        self.assertTrue(results["B_EX_START_BLOCKED"])
        self.assertTrue(results["B_SH_WITH_DISTINCT_A_PRESTART_PASS"])
        self.assertEqual(action.call_count, 3)

    def test_check_reload_covers_blocked_and_allowed(self) -> None:
        """A reload must test both B-exclusive fail and free success."""
        values = {}
        with (
            patch.object(QA, "assert_action") as action,
            patch.object(
                QA, "read_events", side_effect=[["start"], ["start", "reload"]]
            ),
        ):
            QA.check_reload(self.root, "qa.service", values)
        self.assertTrue(values["B_EX_RELOAD_BLOCKED"])
        self.assertTrue(values["B_FREE_RELOAD_PASS"])
        self.assertEqual(action.call_count, 2)

    def test_check_prestart_bounded_a_and_recovery(self) -> None:
        """A EX must block prestart until maintenance releases it."""
        values = {}
        with (
            patch.object(QA, "assert_action") as action,
            patch.object(
                QA,
                "read_events",
                side_effect=[
                    ["start", "reload"],
                    ["start", "reload", "start"],
                ],
            ),
        ):
            QA.check_prestart(self.root, "qa.service", values)
        self.assertTrue(values["A_EX_BOUNDED_PRESTART_BLOCKED"])
        self.assertTrue(values["A_RELEASE_RESTART_PASS"])
        self.assertEqual(action.call_count, 3)

    def test_cli_handles_rejected_guest(self) -> None:
        """The exact run flag is never a bypass around host checks."""
        with patch.object(
            QA, "check_guest", side_effect=RuntimeError("QA_BLOCKED")
        ):
            code, output = self.cli(
                "--run-disposable", "--confirm", QA.CONFIRM
            )
        self.assertEqual(code, 3)
        self.assertIn("QA_BLOCKED", output)

    def test_json_result_excludes_approval(self) -> None:
        """A successful disposable report cannot authorize release."""
        with patch.object(
            QA,
            "run_disposable",
            return_value={
                "status": "PASS_DISPOSABLE_SYSTEMD_BA_OWNER",
                "production_modified": False,
                "owner_maintenance_authorized": False,
            },
        ):
            code, output = self.cli(
                "--run-disposable", "--confirm", QA.CONFIRM, "--jsonl"
            )
        self.assertEqual(code, 0)
        self.assertFalse(json.loads(output)["production_modified"])

    def test_exclusive_b_prevents_shared_probe(self) -> None:
        """Real private flock excludes another shared holder."""
        with (
            (self.root / "maintenance.lock").open("rb") as handle,
            (self.root / "maintenance.lock").open("rb") as second,
            self.assertRaises(BlockingIOError),
        ):
            fcntl.flock(handle, fcntl.LOCK_EX)
            fcntl.flock(second, fcntl.LOCK_SH | fcntl.LOCK_NB)

    def test_no_production_path_in_test_unit(self) -> None:
        """Synthetic test unit must never invoke actual production."""
        for filename in ("maintenance.lock", "updater.lock"):
            (self.root / filename).unlink()
        with patch.object(QA, "UNIT_ROOT", self.root):
            unit = QA.prepare_unit(self.root, "svt-qa-ba-owner-safe.service")
        content = unit.read_text()
        self.assertNotIn("/opt/shelly-voltage", content)
        self.assertNotIn("shelly-voltage-bot", content)


if __name__ == "__main__":
    unittest.main()
