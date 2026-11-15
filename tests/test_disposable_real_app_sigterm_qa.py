"""Default-inert regression tests for exact LXC114 real app SIGTERM QA."""

from __future__ import annotations

import contextlib
import fcntl
import importlib.util
import io
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "disposable-real-app-sigterm-qa.py"
SPEC = importlib.util.spec_from_file_location("qa_app_sigterm", SCRIPT)
QA = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = QA
SPEC.loader.exec_module(QA)


class RealAppSigtermHarnessTests(unittest.TestCase):
    """Never start Docker or production services from these unit tests."""

    def setUp(self) -> None:
        """Create an invented root under an isolated temporary directory."""
        temp = tempfile.TemporaryDirectory(prefix="sigterm-unit-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def invoke(self, *args: str) -> int:
        """Run default-inert CLI and capture its output."""
        with (
            patch.object(sys, "argv", ["qa", *args]),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            return QA.main()

    def test_default_does_not_touch_docker(self) -> None:
        """Default startup must never import or run the Docker guard."""
        with patch.object(QA, "load_qa", side_effect=AssertionError):
            self.assertEqual(self.invoke(), 0)

    def test_generic_consent_rejected(self) -> None:
        """Only the full offline QA confirmation may run the experiment."""
        with patch.object(QA, "load_qa", side_effect=AssertionError):
            self.assertEqual(
                self.invoke("--run-disposable", "--confirm", "yes"), 2
            )

    def test_guard_is_exact_adjacent_qa_file(self) -> None:
        """A wrong filename cannot silently select another host guard."""
        self.assertEqual(QA.GUARD.parent, SCRIPT.parent)
        self.assertTrue(QA.GUARD.is_file())
        self.assertTrue(QA.APP.is_file())

    def test_env_disables_telegram_and_external_meter(self) -> None:
        """All QA connection destinations remain invented or loopback."""
        values = QA.docker_env()
        mapping = {
            arg.split("=", 1)[0]: arg.split("=", 1)[1]
            for arg in values
            if "=" in arg
        }
        self.assertEqual(mapping["TELEGRAM_TOKEN"], "")
        self.assertEqual(mapping["TELEGRAM_CHAT_ID"], "")
        self.assertEqual(mapping["SHELLY_HOST"], "127.0.0.1")
        self.assertEqual(mapping["DATABASE_PATH"], "/synthetic/qa.sqlite3")
        self.assertEqual(mapping["SHELLY_WRITER_FENCE"], "required")

    def test_docker_mounts_are_qa_only(self) -> None:
        """No live database, network or host secret mount is possible."""
        helper = SimpleNamespace(
            base_flags=lambda name: [
                "docker",
                "run",
                "--network",
                "none",
                "--restart",
                "no",
                "--label",
                "org.svt.qa.fence=" + name,
            ],
            IMAGE="sha256:qa-invented",
        )
        args = QA.docker_args(self.root, "qa-owned", helper)
        self.assertIn("/synthetic", " ".join(args))
        self.assertIn("/run/shelly-owner", " ".join(args))
        self.assertIn(str(self.root), " ".join(args))
        self.assertNotIn("/var/lib/shelly-voltage", " ".join(args))
        self.assertIn("--entrypoint", args)
        self.assertEqual(args[-2:], ["/qa-code/app.py", "run"])

    def test_host_lock_exclusivity(self) -> None:
        """A synthetic B SH denies a second EX probe."""
        lock = self.root / "B"
        lock.touch(mode=0o600)
        self.assertTrue(QA.probe_exclusive(lock))
        with lock.open("rb") as held:
            fcntl.flock(held, fcntl.LOCK_SH | fcntl.LOCK_NB)
            self.assertFalse(QA.probe_exclusive(lock))

    def test_missing_sqlite_denied(self) -> None:
        """An absent database can never count as a valid recovery."""
        with self.assertRaisesRegex(RuntimeError, "NOT_CREATED"):
            QA.sql_integrity(self.root)

    def test_synthetic_sqlite_quickcheck(self) -> None:
        """Only a scratch database can be checked."""
        folder = self.root / "synthetic"
        folder.mkdir()
        with closing(sqlite3.connect(folder / "qa.sqlite3")) as database:
            database.execute("CREATE TABLE first(v INTEGER)")
            database.execute("CREATE TABLE second(v INTEGER)")
        self.assertEqual(QA.sql_integrity(self.root), ("ok", 2))

    def test_insufficient_schema_is_denied(self) -> None:
        """SQLite quick_check alone is not enough without schema."""
        folder = self.root / "synthetic"
        folder.mkdir()
        with closing(sqlite3.connect(folder / "qa.sqlite3")) as database:
            database.execute("CREATE TABLE single(v INTEGER)")
        with self.assertRaisesRegex(RuntimeError, "QUICK_CHECK_FAILED"):
            QA.sql_integrity(self.root)

    def test_wait_state_checks_no_autorestart_policy(self) -> None:
        """Any mismatched Docker restart policy blocks QA classification."""
        helper = SimpleNamespace(
            command=lambda _args: SimpleNamespace(
                returncode=0, stdout="true|unless-stopped"
            )
        )
        with self.assertRaisesRegex(RuntimeError, "AUTORESTART_POLICY"):
            QA.wait_state(helper, "qa-owned", True)

    def test_wait_state_expected_stop(self) -> None:
        """A stopped no-restart QA container completes state verification."""
        helper = SimpleNamespace(
            command=lambda _args: SimpleNamespace(
                returncode=0, stdout="false|no"
            )
        )
        QA.wait_state(helper, "qa-owned", False)

    def test_failed_start_cleans_only_owned_object(self) -> None:
        """A failed startup cleans only the disposable QA object."""
        calls: list[str] = []

        def refuse(_args: list[str], timeout: int = 12) -> SimpleNamespace:
            """Return a fake Docker start error with no external action."""
            return SimpleNamespace(returncode=1, stderr="refused")

        helper = SimpleNamespace(
            base_flags=lambda name: ["docker", "run", name],
            IMAGE="sha256:invented",
            command=refuse,
            remove_owned=lambda name: calls.append(name),
        )
        with self.assertRaisesRegex(RuntimeError, "START_FAILED"):
            QA.run_case(self.root, helper)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].startswith("svt-qa-fence-sigterm-"))


if __name__ == "__main__":
    unittest.main()
