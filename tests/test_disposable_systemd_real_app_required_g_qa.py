"""Unit safety checks for transient systemd real-app required-g QA."""

from __future__ import annotations

import contextlib
import fcntl
import importlib.util
import io
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/disposable-systemd-real-app-required-g-qa.py"
SPEC = importlib.util.spec_from_file_location("qa_systemd_real_g", SCRIPT)
QA = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = QA
SPEC.loader.exec_module(QA)


def result(code: int, text: str = "") -> SimpleNamespace:
    """Create a fake command result without running Docker or systemd."""
    return SimpleNamespace(returncode=code, stdout=text, stderr="")


class RealSystemdGTests(unittest.TestCase):
    """All unit fixtures operate only under a disposable temporary root."""

    def setUp(self) -> None:
        """Make invented G/B/W lock files without external services."""
        temp = tempfile.TemporaryDirectory(prefix="systemd-g-unit-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / "locks").mkdir()
        (self.root / "synthetic").mkdir()
        for name in ("G", "B", "W"):
            (self.root / "locks" / name).touch(mode=0o600)

    def invoke(self, *args: str) -> int:
        """Forbid QA host inspection unless exact consent is supplied."""
        with (
            patch.object(sys, "argv", ["qa", *args]),
            patch.object(QA, "load_qa", side_effect=AssertionError),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            return QA.main()

    def test_default_is_inert(self) -> None:
        """No flags never create transient systemd units or Docker objects."""
        self.assertEqual(self.invoke(), 0)

    def test_generic_consent_refused(self) -> None:
        """An unspecified yes cannot activate isolated integration."""
        self.assertEqual(
            self.invoke("--run-disposable", "--confirm", "yes"), 2
        )

    def test_uses_only_adjacent_fixed_offline_harness(self) -> None:
        """Do not import a mutable external or production helper path."""
        self.assertEqual(QA.APP_QA.parent, SCRIPT.parent)
        self.assertTrue(QA.APP_QA.is_file())

    def test_unique_transient_names_are_restricted(self) -> None:
        """Only QA-labelled transient systemd units are permitted."""
        for name in QA.unit_names(self.root).values():
            self.assertTrue(name.startswith("svt-qa-systemd-g-"))

    def test_unique_docker_names_are_restricted(self) -> None:
        """All disposable containers must use the exact owner label prefix."""
        for name in QA.docker_names(self.root).values():
            self.assertTrue(name.startswith("svt-qa-fence-sys-g-"))

    def test_systemd_properties_and_flock_order(self) -> None:
        """Transient service holds G SH before launching offline Docker."""
        captured: list[str] = []
        test = SimpleNamespace(
            app_args=lambda root, name, app, qa: [
                "docker",
                "run",
                "--network",
                "none",
                "--restart",
                "no",
                "--label",
                name,
            ]
        )

        def fake_run(args: list[str], **kwargs: object) -> object:
            """Capture the exact unit invocation without executing it."""
            captured.extend(args)
            return result(0)

        with patch.object(QA.subprocess, "run", side_effect=fake_run):
            QA.launch(test, object(), object(), self.root, "qa-unit", "qa-box")
        self.assertEqual(captured[0], "systemd-run")
        self.assertIn("PrivateNetwork=yes", captured)
        self.assertIn("ProtectSystem=strict", captured)
        self.assertIn("--shared", captured)
        self.assertIn("--nonblock", captured)
        self.assertIn(str(self.root / "locks/G"), captured)
        self.assertLess(
            captured.index(str(self.root / "locks/G")),
            captured.index("/usr/bin/docker"),
        )

    def test_G_EX_rejects_systemd_before_container(self) -> None:
        """An external maintenance gate blocks a simulated unit start."""
        test = SimpleNamespace()
        helper = SimpleNamespace(
            command=lambda args: result(1),
        )
        with patch.object(QA, "launch", return_value=result(1)):
            QA.negative_case(test, object(), helper, self.root, "u", "c")

    def test_systemd_denial_must_not_create_container(self) -> None:
        """A failed unit is unsafe if its Docker object was still created."""
        helper = SimpleNamespace(command=lambda args: result(0))
        with (
            patch.object(QA, "launch", return_value=result(1)),
            self.assertRaisesRegex(RuntimeError, "CONTAINER_CREATED"),
        ):
            QA.negative_case(object(), object(), helper, self.root, "u", "c")

    def test_systemd_denial_must_not_create_sqlite(self) -> None:
        """A denied unit cannot be counted as safe after writing SQLite."""
        (self.root / "synthetic/qa.sqlite3").touch()
        helper = SimpleNamespace(command=lambda args: result(1))
        with (
            patch.object(QA, "launch", return_value=result(1)),
            self.assertRaisesRegex(RuntimeError, "SQLITE_CREATED"),
        ):
            QA.negative_case(object(), object(), helper, self.root, "u", "c")

    def test_reject_false_systemd_success_under_G_EX(self) -> None:
        """Exit zero while admission is blocked is a safety failure."""
        helper = SimpleNamespace()
        with (
            patch.object(QA, "launch", return_value=result(0)),
            self.assertRaisesRegex(RuntimeError, "NOT_DENIED"),
        ):
            QA.negative_case(object(), object(), helper, self.root, "u", "c")

    def test_incumbent_B_W_must_remain_locked(self) -> None:
        """Actual kernel B SH and W EX exclude maintenance probes."""
        probe = SimpleNamespace(probe_exclusive=qa_probe)
        with (
            (self.root / "locks/B").open("rb") as b,
            (self.root / "locks/W").open("rb") as w,
        ):
            fcntl.flock(b, fcntl.LOCK_SH | fcntl.LOCK_NB)
            fcntl.flock(w, fcntl.LOCK_EX | fcntl.LOCK_NB)
            QA.ensure_admission(probe, self.root)

    def test_missing_B_lock_denied(self) -> None:
        """A running app without lifetime B SH is not safe."""
        probe = SimpleNamespace(probe_exclusive=qa_probe)
        with self.assertRaisesRegex(RuntimeError, "LIFETIME_LOCK"):
            QA.ensure_admission(probe, self.root)

    def test_terminate_refuses_unowned_container(self) -> None:
        """The transient test cannot signal unverified Docker objects."""
        helper = SimpleNamespace(owner_label=lambda name: False)
        with self.assertRaisesRegex(RuntimeError, "NOT_OWNER_LABELLED"):
            QA.terminate(object(), helper, "unknown")

    def test_terminate_requires_clean_exit(self) -> None:
        """A nonzero Docker exit cannot be labelled clean teardown."""
        helper = SimpleNamespace(
            owner_label=lambda name: True,
            command=lambda args: result(0, "70" if "inspect" in args else ""),
        )
        app = SimpleNamespace(wait_state=lambda *args: None)
        with self.assertRaisesRegex(RuntimeError, "EXIT_NOT_ZERO"):
            QA.terminate(app, helper, "qa-owned")

    def test_final_sqlite_quick_check(self) -> None:
        """Only synthetic SQLite is inspected after all actors exit."""
        db = self.root / "synthetic/qa.sqlite3"
        with contextlib.closing(sqlite3.connect(db)) as conn:
            conn.execute("CREATE TABLE sample(v INTEGER)")
            conn.commit()
        probe = SimpleNamespace(probe_exclusive=qa_probe)
        self.assertEqual(QA.validate_final(probe, self.root), "ok")

    def test_run_suite_stops_on_denial_before_positive_case(self) -> None:
        """Negative path always executes before a successful start case."""
        events: list[str] = []
        test = SimpleNamespace(
            prepare=lambda *args: events.append("prepare"),
        )
        helper = SimpleNamespace(
            remove_owned=lambda name: events.append("cleanup"),
        )
        with (
            patch.object(
                QA,
                "negative_case",
                side_effect=lambda *a: events.append("deny"),
            ),
            patch.object(
                QA,
                "positive_case",
                side_effect=lambda *a: events.append("allow") or {"ok": True},
            ),
        ):
            output = QA.run_suite(test, object(), helper)
        self.assertEqual(output, {"ok": True})
        self.assertEqual(events[:3], ["prepare", "deny", "allow"])
        self.assertEqual(events.count("cleanup"), 2)


def qa_probe(path: Path) -> bool:
    """Use only an invented independent host lock file descriptor."""
    with path.open("rb") as holder:
        try:
            fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True


if __name__ == "__main__":
    unittest.main()
