"""Default-inert tests for legacy OCI startup bypass under maintenance G."""

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
SCRIPT = ROOT / "scripts/disposable-legacy-oci-g-bypass-qa.py"
SPEC = importlib.util.spec_from_file_location("legacy_g_qa", SCRIPT)
QA = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = QA
SPEC.loader.exec_module(QA)


class LegacyWriterBypassTests(unittest.TestCase):
    """Never run Docker or connect to a physical meter from unit tests."""

    def setUp(self) -> None:
        """Create only private invented G and synthetic SQLite directories."""
        temporary = tempfile.TemporaryDirectory(prefix="old-oci-g-unit-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "locks").mkdir()
        (self.root / "synthetic").mkdir()
        (self.root / "locks/G").touch(mode=0o600)

    def invoke(self, *args: str) -> int:
        """Refuse any host safety probing without explicit test approval."""
        with (
            patch.object(sys, "argv", ["qa", *args]),
            patch.object(QA, "load_qa", side_effect=AssertionError),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            return QA.main()

    def test_default_does_not_touch_docker(self) -> None:
        """No flags must remain a complete no-op."""
        self.assertEqual(self.invoke(), 0)

    def test_untrusted_yes_does_not_activate(self) -> None:
        """Only the full exact LXC114 consent string can run this QA."""
        self.assertEqual(
            self.invoke("--run-disposable", "--confirm", "yes"), 2
        )

    def test_sibling_qa_guard_is_required(self) -> None:
        """Only the preexisting fixed-host offline guard is imported."""
        self.assertEqual(QA.GUARD.parent, SCRIPT.parent)
        self.assertTrue(QA.GUARD.is_file())

    def test_old_env_does_not_invent_new_fence(self) -> None:
        """The old source is tested with the actual unguarded mode off."""
        env = QA.legacy_env()
        self.assertEqual(env["SHELLY_WRITER_FENCE"], "")
        self.assertEqual(env["TELEGRAM_TOKEN"], "")
        self.assertEqual(env["TELEGRAM_CHAT_ID"], "")
        self.assertEqual(env["SHELLY_HOST"], "127.0.0.1")
        self.assertEqual(env["DATABASE_PATH"], "/synthetic/legacy.sqlite3")

    def test_docker_arguments_use_exact_cached_image(self) -> None:
        """Never pull remote images or route traffic to a real meter."""
        helper = SimpleNamespace(
            base_flags=lambda name: [
                "docker",
                "run",
                "-d",
                "--network",
                "none",
                "--restart",
                "no",
                "--user",
                "10001:10001",
            ],
            sandbox_mounts=lambda root: ["--mount", str(root)],
            IMAGE="sha256:synthetic-qa-image",
        )
        args = QA.docker_args(self.root, "qa-unit", helper)
        self.assertIn("none", args)
        self.assertIn("no", args)
        self.assertIn("10001:10001", args)
        self.assertEqual(
            args[-4:],
            ["sha256:synthetic-qa-image", "python", "/app/app.py", "run"],
        )

    def test_valid_invented_sqlite_schema(self) -> None:
        """A test cannot pass on an empty SQLite header alone."""
        path = self.root / "synthetic/legacy.sqlite3"
        with contextlib.closing(sqlite3.connect(path)) as db:
            db.execute("CREATE TABLE first(v INTEGER)")
            db.execute("CREATE TABLE second(v INTEGER)")
            db.commit()
        self.assertEqual(QA.read_database(path), 2)
        self.assertEqual(QA.wait_for_schema(path), 2)

    def test_empty_sqlite_schema_rejected(self) -> None:
        """An empty database file cannot prove successful legacy writing."""
        path = self.root / "synthetic/legacy.sqlite3"
        with contextlib.closing(sqlite3.connect(path)):
            pass
        with self.assertRaisesRegex(RuntimeError, "NOT_VALID"):
            QA.read_database(path)

    def test_missing_committed_schema_has_bounded_timeout(self) -> None:
        """A broken old app cannot cause an unbounded QA wait."""
        path = self.root / "synthetic/legacy.sqlite3"
        with (
            patch.object(QA.time, "monotonic", side_effect=[100, 200]),
            self.assertRaisesRegex(RuntimeError, "NOT_OBSERVED"),
        ):
            QA.wait_for_schema(path)

    def test_host_G_exclusive_blocks_another_G_shared(self) -> None:
        """The negative test really owns a kernel maintenance G EX."""
        with (self.root / "locks/G").open("rb") as holder:
            fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with (
                (self.root / "locks/G").open("rb") as other,
                self.assertRaises(BlockingIOError),
            ):
                fcntl.flock(other, fcntl.LOCK_SH | fcntl.LOCK_NB)

    def test_failed_old_container_start_cleans_owner(self) -> None:
        """Even an unsuccessful launch removes only the QA-labelled object."""
        removed: list[str] = []
        helper = SimpleNamespace(
            base_flags=lambda name: ["docker", "run", name],
            sandbox_mounts=lambda root: [],
            IMAGE="sha256:qa",
            command=lambda args, timeout: SimpleNamespace(returncode=1),
            remove_owned=lambda name: removed.append(name),
        )
        with self.assertRaisesRegex(RuntimeError, "START_FAILED"):
            QA.proof(self.root, "svt-qa-fence-legacy-g-test", helper)
        self.assertEqual(removed, ["svt-qa-fence-legacy-g-test"])

    def test_real_negative_proof_is_never_release_go(self) -> None:
        """A simulated SQLite write while G EX held is a release blocker."""
        removed: list[str] = []
        helper = SimpleNamespace(
            base_flags=lambda name: ["docker", "run", name],
            sandbox_mounts=lambda root: [],
            IMAGE="sha256:qa",
            command=lambda args, timeout: SimpleNamespace(returncode=0),
            remove_owned=lambda name: removed.append(name),
        )
        with (
            patch.object(
                QA,
                "wait_for_database",
                return_value=self.root / "synthetic/legacy.sqlite3",
            ),
            patch.object(QA, "wait_for_schema", return_value=6),
        ):
            result = QA.proof(self.root, "svt-qa-fence-legacy-g-test", helper)
        self.assertEqual(result["release_decision"], "NO_GO")
        self.assertEqual(result["sqlite_tables"], 6)
        self.assertEqual(len(removed), 1)

    def test_guard_runs_before_actual_replay(self) -> None:
        """A verified host guard is mandatory even after exact consent."""
        checks: list[str] = []
        fake = SimpleNamespace(
            guest_guard=lambda: checks.append("guard"),
        )
        with (
            patch.object(
                sys,
                "argv",
                ["qa", "--run-disposable", "--confirm", QA.CONFIRM],
            ),
            patch.object(QA, "load_qa", return_value=fake),
            patch.object(
                QA,
                "run_suite",
                side_effect=lambda qa: checks.append("suite") or {"ok": True},
            ),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(QA.main(), 0)
        self.assertEqual(checks, ["guard", "suite"])

    def test_error_fails_closed_and_does_not_claim_go(self) -> None:
        """An unexpected fixture exception never authorizes deployment."""
        fake = SimpleNamespace(guest_guard=lambda: None)
        with (
            patch.object(
                sys,
                "argv",
                ["qa", "--run-disposable", "--confirm", QA.CONFIRM],
            ),
            patch.object(QA, "load_qa", return_value=fake),
            patch.object(
                QA, "run_suite", side_effect=RuntimeError("invented")
            ),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(QA.main(), 3)
        self.assertIn("QA_BLOCKED", output.getvalue())
        self.assertNotIn("GO", output.getvalue())


if __name__ == "__main__":
    unittest.main()
