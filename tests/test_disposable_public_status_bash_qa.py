"""Default-inert unit coverage for the disposable Bash status candidate."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sqlite3
import stat
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/disposable-public-status-bash-qa.py"
)
SPEC = importlib.util.spec_from_file_location("public_status_bash_qa", SCRIPT)
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)


class PublicStatusBashTests(unittest.TestCase):
    """Use only invented paths, metadata and an in-memory process result."""

    def setUp(self) -> None:
        """Create private paths without running source or Docker commands."""
        temp = tempfile.TemporaryDirectory(prefix="qa-bash-status-unit-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        candidate = self.root / "candidate.sh"
        candidate.write_text("#!/bin/bash\n")
        context = patch.object(QA, "CANDIDATE", candidate)
        context.start()
        self.addCleanup(context.stop)

    def call_main(self, *args: str) -> tuple[int, str]:
        """Get a CLI result without access to any real service."""
        capture = io.StringIO()
        with (
            patch.object(sys, "argv", ["qa", *args]),
            contextlib.redirect_stdout(capture),
        ):
            code = QA.main()
        return code, capture.getvalue()

    def test_default_is_inert(self) -> None:
        """No confirmation means no network, file or Docker checks."""
        with patch.object(QA, "guard", side_effect=AssertionError):
            code, output = self.call_main()
        self.assertEqual(code, 0)
        self.assertIn("DRY_RUN", output)

    def test_explicit_dry_run_is_inert(self) -> None:
        """An explicit dry run overrides mutation mode."""
        with patch.object(QA, "guard", side_effect=AssertionError):
            code, _ = self.call_main("--dry-run", "--run-disposable")
        self.assertEqual(code, 0)

    def test_confirmation_rejects_generic_yes(self) -> None:
        """A generic acknowledgment is never enough to run QA."""
        with patch.object(QA, "guard", side_effect=AssertionError):
            code, _ = self.call_main("--run-disposable", "--confirm", "yes")
        self.assertEqual(code, 2)

    def test_guest_root_gate(self) -> None:
        """The exact offline guest must be required for real QA."""
        with (
            patch.object(QA.os, "geteuid", return_value=1000),
            self.assertRaisesRegex(RuntimeError, "DISPOSABLE_GUEST_ONLY"),
        ):
            QA.guard()

    def test_guest_network_gate(self) -> None:
        """An external interface must fail before reading candidate files."""
        with (
            patch.object(QA.os, "geteuid", return_value=0),
            patch.object(
                QA.socket,
                "gethostname",
                return_value="shelly-docker-reboot-qa",
            ),
            patch.object(
                QA.socket,
                "if_nameindex",
                return_value=[(1, "lo"), (2, "eth0")],
            ),
            self.assertRaisesRegex(RuntimeError, "EXTERNAL_NETWORK_PRESENT"),
        ):
            QA.guard()

    def test_fixture_only_invented_sqlite(self) -> None:
        """A small synthetic SQLite database starts with two intact rows."""
        db = QA.make_fixture(self.root)
        with closing(sqlite3.connect(db)) as conn:
            self.assertEqual(
                conn.execute("pragma quick_check").fetchone()[0], "ok"
            )
            self.assertEqual(
                conn.execute("select count(*) from readings").fetchone()[0], 2
            )
        public = self.root / "public/deployed.json"
        self.assertEqual(json.loads(public.read_text()), QA.OLD)
        self.assertEqual(stat.S_IMODE(public.stat().st_mode), 0o644)

    def test_environment_is_private(self) -> None:
        """Candidate environment points to the synthetic database."""
        db = self.root / "test.sqlite3"
        env = QA.environment(self.root, "publish_fail", db)
        self.assertEqual(env["SVT_ROOT"], str(self.root))
        self.assertEqual(env["DATABASE_PATH"], str(db))
        self.assertEqual(env["QA_MODE"], "publish_fail")

    def test_success_requires_no_pending(self) -> None:
        """A public/private match with exit zero is a success case only."""
        db = QA.make_fixture(self.root)
        for name in ("public", "state"):
            (self.root / name / "deployed.json").write_text(json.dumps(QA.NEW))
        process = SimpleNamespace(
            returncode=0, stdout="UPDATE PASS: 0.5.21-hw", stderr=""
        )
        report = QA.assert_outcome(self.root, db, "success", process)
        self.assertFalse(report["pending"])

    def test_public_failure_preserves_previous_state(self) -> None:
        """A failed public write preserves pending and old public JSON."""
        db = QA.make_fixture(self.root)
        (self.root / "state/deployed.json").write_text(json.dumps(QA.NEW))
        (self.root / "state/pending-update").write_text("synthetic")
        process = SimpleNamespace(returncode=71, stdout="", stderr="fail")
        report = QA.assert_outcome(self.root, db, "publish_fail", process)
        self.assertTrue(report["pending"])
        self.assertEqual(report["public"], QA.OLD["version"])

    def test_pending_fsync_failure_is_not_success(self) -> None:
        """Return nonzero even when pending is already absent."""
        db = QA.make_fixture(self.root)
        for name in ("public", "state"):
            (self.root / name / "deployed.json").write_text(json.dumps(QA.NEW))
        process = SimpleNamespace(returncode=73, stdout="", stderr="fail")
        report = QA.assert_outcome(
            self.root, db, "pending_fsync_fail", process
        )
        self.assertFalse(report["pending"])

    def test_pinned_candidate_is_not_live_install(self) -> None:
        """This harness names no production source destination."""
        self.assertTrue(QA.CANDIDATE.is_relative_to(self.root))
        self.assertNotIn("docker compose up", QA.BASH)


if __name__ == "__main__":
    unittest.main()
