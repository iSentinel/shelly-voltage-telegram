"""Default-inert unit tests for actual real app required-g Docker replay."""

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
SCRIPT = ROOT / "scripts/disposable-real-app-required-g-qa.py"
SPEC = importlib.util.spec_from_file_location("qa_real_app_g", SCRIPT)
QA = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = QA
SPEC.loader.exec_module(QA)


class RealAppGReplayTests(unittest.TestCase):
    """Never start Docker, call Shelly or read a real SQLite database."""

    def setUp(self) -> None:
        """Construct only a private invented root for lock comparisons."""
        temp = tempfile.TemporaryDirectory(prefix="realapp-g-unit-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / "locks").mkdir()
        (self.root / "synthetic").mkdir()
        (self.root / "locks/G").touch(mode=0o600)

    def command_line(self, *args: str) -> int:
        """Require user consent before importing the Docker host guard."""
        with (
            patch.object(sys, "argv", ["qa", *args]),
            patch.object(QA, "load_existing", side_effect=AssertionError),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            return QA.main()

    def test_default_is_noop(self) -> None:
        """No flags never import the Docker helper or perform any action."""
        self.assertEqual(self.command_line(), 0)

    def test_generic_consent_is_refused(self) -> None:
        """A confirmation must match the exact offline QA phrase."""
        self.assertEqual(
            self.command_line("--run-disposable", "--confirm", "yes"), 2
        )

    def test_sibling_harness_required(self) -> None:
        """Only our already reviewed fixed-host application QA is loaded."""
        self.assertEqual(QA.EXISTING.parent, SCRIPT.parent)
        self.assertTrue(QA.EXISTING.is_file())

    def test_explicit_required_g_in_container_env(self) -> None:
        """Legacy SIGTERM helper keeps required; this QA enables required-g."""
        base = SimpleNamespace(
            docker_args=lambda root, name, helper: [
                "docker",
                "run",
                "-d",
                "--network",
                "none",
                "--restart",
                "no",
                "--read-only",
                "--user",
                "10001:10001",
                "--label",
                "org.svt.qa.fence=" + name,
                "SHELLY_WRITER_FENCE=required",
            ],
        )
        result = QA.app_args(self.root, "qa-test", base, object())
        self.assertEqual(result.count("SHELLY_WRITER_FENCE=required-g"), 1)
        self.assertNotIn("SHELLY_WRITER_FENCE=required", result)
        self.assertIn("none", result)
        self.assertIn("no", result)

    def test_missing_mode_is_denied(self) -> None:
        """Inconsistent environment cannot silently disable admission."""
        base = SimpleNamespace(
            docker_args=lambda root, name, helper: ["docker", "run"],
        )
        with self.assertRaisesRegex(RuntimeError, "ENV_MODE_MISSING"):
            QA.app_args(self.root, "qa-test", base, object())

    def test_exclusive_G_fails_while_shared(self) -> None:
        """A distinct host file descriptor experiences actual kernel G SH."""
        with (self.root / "locks/G").open("rb") as held:
            fcntl.flock(held, fcntl.LOCK_SH | fcntl.LOCK_NB)
            self.assertFalse(QA.probe_exclusive(self.root / "locks/G"))
        self.assertTrue(QA.probe_exclusive(self.root / "locks/G"))

    def test_denied_G_uses_direct_foreground_error(self) -> None:
        """The actual denial must be checked without Docker logging driver."""
        seen: list[list[str]] = []
        base = SimpleNamespace(
            docker_args=lambda root, name, helper: [
                "docker",
                "run",
                "-d",
                "--network",
                "none",
                "SHELLY_WRITER_FENCE=required",
            ],
        )

        def fake_command(args: list[str], timeout: int) -> object:
            """Capture only invented Docker command and stderr."""
            seen.append(args)
            return SimpleNamespace(
                returncode=1,
                stdout="",
                stderr="WriterFenceError: owner_fence_already_held",
            )

        helper = SimpleNamespace(command=fake_command)
        QA.denied_under_g_ex(self.root, base, helper)
        self.assertNotIn("-d", seen[0])
        self.assertEqual(seen[0][2], "--rm")

    def test_denied_G_requires_reason_not_generic_error(self) -> None:
        """An unrelated startup error is not proof of G admission."""
        base = SimpleNamespace(
            docker_args=lambda root, name, helper: [
                "docker",
                "run",
                "-d",
                "SHELLY_WRITER_FENCE=required",
            ],
        )
        helper = SimpleNamespace(
            command=lambda args, timeout: SimpleNamespace(
                returncode=1,
                stdout="",
                stderr="missing app.py",
            )
        )
        with self.assertRaisesRegex(RuntimeError, "DENIAL_REASON_WRONG"):
            QA.denied_under_g_ex(self.root, base, helper)

    def test_denied_G_fails_if_sqlite_was_created(self) -> None:
        """A failed admission cannot write even one invented SQLite file."""
        (self.root / "synthetic/qa.sqlite3").touch()
        base = SimpleNamespace(
            docker_args=lambda root, name, helper: [
                "docker",
                "run",
                "-d",
                "SHELLY_WRITER_FENCE=required",
            ],
        )
        helper = SimpleNamespace(
            command=lambda args, timeout: SimpleNamespace(
                returncode=1,
                stdout="",
                stderr="owner_fence_already_held",
            )
        )
        with self.assertRaisesRegex(RuntimeError, "SQLITE_CREATED"):
            QA.denied_under_g_ex(self.root, base, helper)

    def test_sqlite_wait_is_bounded(self) -> None:
        """Missing invented SQLite is a hard failure, not success."""
        with (
            patch.object(QA.time, "monotonic", side_effect=[100, 200]),
            self.assertRaisesRegex(RuntimeError, "SQLITE_NOT_CREATED"),
        ):
            QA.await_sqlite(self.root)

    def test_successful_admitted_app_removes_only_owned_name(self) -> None:
        """Even a failed start must clean only its own QA label."""
        names: list[str] = []
        base = SimpleNamespace(
            docker_args=lambda root, name, helper: [
                "docker",
                "run",
                "-d",
                "SHELLY_WRITER_FENCE=required",
            ],
            wait_state=lambda *_args: None,
        )
        helper = SimpleNamespace(
            command=lambda args, timeout: SimpleNamespace(returncode=1),
            remove_owned=lambda name: names.append(name),
        )
        with self.assertRaisesRegex(RuntimeError, "REAL_APP_START_FAILED"):
            QA.admitted_app(self.root, base, helper)
        self.assertEqual(len(names), 1)
        self.assertTrue(names[0].startswith("svt-qa-fence-g-running-"))

    def test_suite_denial_precedes_normal_run(self) -> None:
        """The success case must not mask a blocked-denial test."""
        calls: list[str] = []
        with (
            patch.object(
                QA, "prepare", side_effect=lambda *_: calls.append("prepare")
            ),
            patch.object(
                QA,
                "denied_under_g_ex",
                side_effect=lambda *_: calls.append("deny"),
            ),
            patch.object(
                QA,
                "admitted_app",
                side_effect=lambda *_: calls.append("admit") or {"ok": True},
            ),
        ):
            result = QA.run_suite(self.root, object(), object())
        self.assertEqual(calls, ["prepare", "deny", "admit"])
        self.assertEqual(result, {"ok": True})


if __name__ == "__main__":
    unittest.main()
