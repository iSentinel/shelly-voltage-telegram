"""Default-inert QA checks for legacy Docker owner restart freeze ordering."""

from __future__ import annotations

import contextlib
import fcntl
import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/disposable-legacy-owner-freeze-qa.py"
SPEC = importlib.util.spec_from_file_location("qa_old_owner_freeze", SCRIPT)
QA = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = QA
SPEC.loader.exec_module(QA)


def result(code: int = 0, out: str = "") -> SimpleNamespace:
    """Construct a fixed invented command result without starting Docker."""
    return SimpleNamespace(returncode=code, stdout=out, stderr="")


class LegacyFreezeTests(unittest.TestCase):
    """No unit test may contact production or execute Docker."""

    def setUp(self) -> None:
        """Create only invented QA lock files in a temporary directory."""
        temp = tempfile.TemporaryDirectory(prefix="legacy-freeze-unit-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        folder = self.root / "locks"
        folder.mkdir()
        for name in ("G", "B", "W"):
            (folder / name).touch(mode=0o600)

    def invoke(self, *args: str) -> int:
        """Guard both default and invalid consent paths from guest probes."""
        with (
            patch.object(sys, "argv", ["qa", *args]),
            patch.object(QA, "load_legacy", side_effect=AssertionError),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            return QA.main()

    def test_inert_without_arguments(self) -> None:
        """No default code path may run a Docker command."""
        self.assertEqual(self.invoke(), 0)

    def test_invalid_consent_refused(self) -> None:
        """An arbitrary confirmation cannot enable the QA scenario."""
        self.assertEqual(
            self.invoke("--run-disposable", "--confirm", "yes"), 2
        )

    def test_sibling_guard_only(self) -> None:
        """The test must import the exact fixed LXC114 source guard."""
        self.assertEqual(QA.LEGACY.parent, SCRIPT.parent)
        self.assertTrue(QA.LEGACY.is_file())

    def test_probe_exclusive_with_actual_kernel_lock(self) -> None:
        """Another G EX description is denied when host G SH is held."""
        with (self.root / "locks/G").open("rb") as holder:
            fcntl.flock(holder, fcntl.LOCK_SH | fcntl.LOCK_NB)
            self.assertFalse(QA.probe_exclusive(self.root / "locks/G"))
        self.assertTrue(QA.probe_exclusive(self.root / "locks/G"))

    def test_inspect_requires_exact_owner(self) -> None:
        """Unlabelled Docker objects may not be inspected for a freeze."""
        helper = SimpleNamespace(owner_label=lambda name: False)
        with self.assertRaisesRegex(RuntimeError, "NOT_OUR"):
            QA.inspect_owner(helper, "unrelated")

    def test_inspect_parse_fails_closed(self) -> None:
        """Malformed policy/state output must not be treated as frozen."""
        helper = SimpleNamespace(
            owner_label=lambda name: True,
            command=lambda args: result(0, "garbage"),
        )
        with self.assertRaisesRegex(RuntimeError, "BAD_FORMAT"):
            QA.inspect_owner(helper, "qa-test")

    def test_inspect_reports_policy_running_and_restarts(self) -> None:
        """One actual Docker template is parsed without guesswork."""
        helper = SimpleNamespace(
            owner_label=lambda name: True,
            command=lambda args: result(0, "true|unless-stopped|4"),
        )
        self.assertEqual(
            QA.inspect_owner(helper, "qa-test"),
            (True, "unless-stopped", 4),
        )

    def test_policy_rejects_unexpected_value(self) -> None:
        """QA cannot silently enable restart=always or on-failure."""
        with self.assertRaisesRegex(RuntimeError, "NOT_ALLOWED"):
            QA.restart_policy(object(), "qa", "always")

    def test_policy_requires_owner_label(self) -> None:
        """Changing policy of unowned Docker must be impossible."""
        helper = SimpleNamespace(owner_label=lambda name: False)
        with self.assertRaisesRegex(RuntimeError, "NOT_OUR"):
            QA.restart_policy(helper, "unknown", "no")

    def test_restart_update_failure_is_hard_error(self) -> None:
        """No stopped proof is accepted if docker update failed."""
        helper = SimpleNamespace(
            owner_label=lambda name: True,
            command=lambda args: result(1),
        )
        with self.assertRaisesRegex(RuntimeError, "UPDATE_FAILED"):
            QA.restart_policy(helper, "qa-test", "no")

    def test_mismatched_restart_policy_is_denied(self) -> None:
        """Exit zero from update is not enough without inspect parity."""
        calls: list[list[str]] = []

        def fake(args: list[str]) -> SimpleNamespace:
            """Return an older policy even after an update command."""
            calls.append(args)
            if "inspect" in args:
                return result(0, "true|unless-stopped|0")
            return result()

        helper = SimpleNamespace(
            owner_label=lambda name: True,
            command=fake,
        )
        with self.assertRaisesRegex(RuntimeError, "NOT_APPLIED"):
            QA.restart_policy(helper, "qa-test", "no")
        self.assertTrue(any("update" in command for command in calls))

    def test_stop_before_restart_no_refused(self) -> None:
        """The old writer must never be stopped with restart enabled."""
        helper = SimpleNamespace(
            owner_label=lambda name: True,
            command=lambda args: result(0, "true|unless-stopped|0"),
        )
        with self.assertRaisesRegex(RuntimeError, "UNSAFE_STOP"):
            QA.stop_owned(helper, "qa-test")

    def test_stop_only_after_restarts_disabled(self) -> None:
        """A verified stopped-policy change permits exactly one QA stop."""
        actions: list[list[str]] = []

        def fake(args: list[str], timeout: int = 0) -> SimpleNamespace:
            """Capture only a mocked Docker stop call."""
            actions.append(args)
            return result(0, "true|no|0" if "inspect" in args else "")

        helper = SimpleNamespace(
            owner_label=lambda name: True,
            command=fake,
        )
        QA.stop_owned(helper, "qa-test")
        self.assertEqual(
            [x[:2] for x in actions if "stop" in x],
            [["docker", "stop"]],
        )

    def test_autorestart_during_observation_fails(self) -> None:
        """Any unexpected running state after stop is a release blocker."""
        with (
            patch.object(QA, "inspect_owner", return_value=(True, "no", 0)),
            self.assertRaisesRegex(RuntimeError, "RESTARTED"),
        ):
            QA.verify_stopped(object(), "qa-test", 0)

    def test_restart_count_change_fails(self) -> None:
        """Recreation or restarted Docker owner invalidates the proof."""
        with (
            patch.object(QA, "inspect_owner", return_value=(False, "no", 2)),
            self.assertRaisesRegex(RuntimeError, "RESTARTED"),
        ):
            QA.verify_stopped(object(), "qa-test", 1)

    def test_positive_frozen_window_is_bounded(self) -> None:
        """Stable frozen old Docker must pass a finite observation window."""
        with (
            patch.object(QA, "inspect_owner", return_value=(False, "no", 0)),
            patch.object(QA.time, "sleep") as sleep,
        ):
            QA.verify_stopped(object(), "qa-test", 0)
        self.assertEqual(sleep.call_count, 10)

    def test_main_checks_guest_guard_before_mutations(self) -> None:
        """Never run the scenario if the offline guest guard is refused."""
        events: list[str] = []
        helper = SimpleNamespace(guest_guard=lambda: events.append("guard"))
        with (
            patch.object(
                sys,
                "argv",
                [
                    "qa",
                    "--run-disposable",
                    "--confirm",
                    QA.CONSENT,
                ],
            ),
            patch.object(QA, "load_legacy", return_value=(object(), helper)),
            patch.object(
                QA,
                "run",
                side_effect=lambda *args: events.append("run") or {"ok": True},
            ),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(QA.main(), 0)
        self.assertEqual(events, ["guard", "run"])

    def test_release_status_is_explicit_no_go(self) -> None:
        """One frozen owner cannot certify every other startup path."""
        legacy = SimpleNamespace(
            docker_args=lambda root, name, helper: ["docker", "run"],
            wait_for_database=lambda root, name, helper: self.root / "db",
            wait_for_schema=lambda path: 6,
        )
        helper = SimpleNamespace(command=lambda args, timeout: result())
        with (
            patch.object(QA, "restart_policy"),
            patch.object(QA, "inspect_owner", return_value=(True, "no", 0)),
            patch.object(QA, "stop_owned"),
            patch.object(QA, "verify_stopped"),
        ):
            outcome = QA.run_with_maintenance(self.root, "qa", legacy, helper)
        self.assertEqual(outcome["release_decision"], "NO_GO")
        self.assertFalse(outcome["all_other_owner_paths_frozen"])

    def test_exception_reports_blocked_not_success(self) -> None:
        """Unexpected Docker error cannot be converted to a release GO."""
        with (
            patch.object(
                sys,
                "argv",
                [
                    "qa",
                    "--run-disposable",
                    "--confirm",
                    QA.CONSENT,
                ],
            ),
            patch.object(
                QA,
                "load_legacy",
                return_value=(
                    object(),
                    SimpleNamespace(
                        guest_guard=lambda: None,
                    ),
                ),
            ),
            patch.object(QA, "run", side_effect=RuntimeError("fake")),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(QA.main(), 3)
        self.assertIn("QA_BLOCKED", output.getvalue())


if __name__ == "__main__":
    unittest.main()
