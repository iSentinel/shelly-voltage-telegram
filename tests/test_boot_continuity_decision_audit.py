"""Boot continuity decision QA: observe only; never authorize real startup."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
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
    / "scripts/boot-continuity-decision-audit.py"
)
SPEC = importlib.util.spec_from_file_location("boot_decision_qa", SCRIPT)
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)
DIGEST = "sha256:" + "a" * 64
PIN = QA.IMAGE + "@" + DIGEST
RUNNING = {
    "image": PIN,
    "running": True,
    "health": "healthy",
    "restart_policy": "unless-stopped",
}


class BootContinuityDecisionTests(unittest.TestCase):
    """All fixture tests use disposable paths and an invented image digest."""

    def setUp(self) -> None:
        """Build a private fake deployed receipt and synthetic SQLite."""
        temporary = tempfile.TemporaryDirectory(
            prefix="svt-boot-decision-unit-",
            dir="/tmp",
        )
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.root = self.directory / "source"
        self.root.mkdir()
        for marker in ("VERSION", "RELEASE_VERSION"):
            (self.root / marker).write_text("0.5.21-hw\n")
        self.state = self.directory / "state"
        self.state.mkdir(mode=0o700)
        self.override = self.state / "override.yaml"
        self.deployed = self.state / "deployed.json"
        self.override.write_text(
            "services:\n  shelly-voltage:\n    image: " + PIN + "\n"
        )
        self.deployed.write_text(
            json.dumps({"version": "0.5.21-hw", "digest": DIGEST})
        )
        os.chmod(self.override, 0o600)
        os.chmod(self.deployed, 0o600)
        self.db = self.directory / "history.sqlite3"
        with closing(sqlite3.connect(self.db)) as db:
            db.execute("CREATE TABLE measurements(id INTEGER,voltage REAL)")
            db.execute("CREATE TABLE outages(id INTEGER,ended_at REAL)")
            db.executemany(
                "INSERT INTO measurements VALUES(?,?)",
                [(i, 222.2) for i in range(50)],
            )
            db.commit()
        self.runtime = self.directory / "runtime.json"
        self.runtime.write_text(json.dumps(RUNNING))

    def report(self, runtime: dict | None = None) -> dict:
        """Read only synthetic receipt, database and runtime input."""
        return QA.evaluate(
            self.root,
            self.state,
            self.db,
            RUNNING if runtime is None else runtime,
        )

    def invoke(self, *args: str) -> tuple[int, str]:
        """Invoke CLI without leaking output or contacting production."""
        out = io.StringIO()
        with (
            patch.object(sys, "argv", [str(SCRIPT), *args]),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            return QA.main(), out.getvalue()

    def test_default_inert_without_file_access(self) -> None:
        """No-mode invocation cannot inspect state, Docker or SQLite."""
        with (
            patch.object(QA, "evaluate", side_effect=AssertionError),
            patch.object(QA, "live_runtime", side_effect=AssertionError),
        ):
            self.assertEqual(self.invoke()[0], 0)
            self.assertEqual(self.invoke("--dry-run")[0], 0)

    def test_no_authority_even_with_exact_running_image(self) -> None:
        """A consistent observation is not a verified signature."""
        plan = self.report()
        self.assertEqual(plan["status"], "OBSERVED_RUNNING_PINNED")
        self.assertEqual(plan["reasons"], [])
        self.assertEqual(plan["current_history_rows"], 50)
        self.assertFalse(plan["boot_start_authorized"])
        self.assertFalse(plan["signature_verified_here"])
        self.assertFalse(plan["source_install_authorized"])

    def test_full_cli_fixture_is_read_only(self) -> None:
        """Explicit QA inputs need no production Docker communication."""
        with patch.object(QA, "live_runtime", side_effect=AssertionError):
            code, output = self.invoke(
                "--runtime-fixture",
                str(self.runtime),
                "--root",
                str(self.root),
                "--state",
                str(self.state),
                "--database",
                str(self.db),
                "--jsonl",
            )
        self.assertEqual(code, 0)
        self.assertEqual(
            json.loads(output)["status"], "OBSERVED_RUNNING_PINNED"
        )

    def test_pending_marker_forces_review(self) -> None:
        """Running old or new image cannot silently clear pending-update."""
        pending = self.state / "pending-update"
        pending.write_text("20261114T123000Z-0.5.21-hw\n")
        os.chmod(pending, 0o600)
        plan = self.report()
        self.assertEqual(plan["status"], "REVIEW_REQUIRED")
        self.assertTrue(plan["pending_marker_preserved"])
        self.assertIn("PENDING_RECOVERY_NEEDS_REVIEW", plan["reasons"])
        self.assertTrue(pending.is_file())

    def test_stopped_writer_never_authorizes_start(self) -> None:
        """Boot continuity requires external owner-reviewed signature proof."""
        runtime = dict(RUNNING, running=False)
        plan = self.report(runtime)
        self.assertIn(
            "WRITER_STOPPED_BOOT_CONTINUATION_UNAPPROVED",
            plan["reasons"],
        )
        self.assertFalse(plan["boot_start_authorized"])

    def test_running_unhealthy_requires_review(self) -> None:
        """A present container is not evidence of ready monitoring."""
        plan = self.report(dict(RUNNING, health="unhealthy"))
        self.assertIn("RUNNING_WRITER_NOT_HEALTHY", plan["reasons"])

    def test_image_receipt_mismatch_is_blocked(self) -> None:
        """A different immutable image cannot silently be selected."""
        self.override.write_text(
            "services:\n  shelly-voltage:\n"
            "    image: " + QA.IMAGE + "@sha256:" + "b" * 64 + "\n"
        )
        self.assertEqual(self.report()["status"], "BLOCKED")

    def test_unpinned_image_is_blocked(self) -> None:
        """The unpinned Compose build fallback is never acceptable."""
        self.override.write_text(
            "services:\n  shelly-voltage:\n    image: latest\n"
        )
        self.assertEqual(self.report()["status"], "BLOCKED")

    def test_missing_override_is_blocked(self) -> None:
        """Missing owner pin may not imply a local Docker build."""
        self.override.unlink()
        self.assertEqual(self.report()["status"], "BLOCKED")

    def test_invalid_receipt_is_blocked(self) -> None:
        """A malformed deployed JSON cannot establish image identity."""
        self.deployed.write_text("{not json")
        self.assertEqual(self.report()["status"], "BLOCKED")

    def test_receipt_digest_is_not_signature_proof(self) -> None:
        """Even a well-formed sha256 digest never becomes signature proof."""
        self.assertFalse(self.report()["signature_verified_here"])
        self.assertTrue(self.report()["signed_digest_verification_required"])

    def test_source_version_mismatch_requires_review(self) -> None:
        """Real source/image drift is reported without attempting repair."""
        (self.root / "VERSION").write_text("0.5.15-hw\n")
        (self.root / "RELEASE_VERSION").write_text("0.5.15-hw\n")
        plan = self.report()
        self.assertIn(
            "INSTALLED_SOURCE_IMAGE_VERSION_MISMATCH", plan["reasons"]
        )
        self.assertFalse(plan["source_install_authorized"])

    def test_runtime_image_mismatch_requires_review(self) -> None:
        """Actual image must match the private selected image reference."""
        plan = self.report(
            dict(
                RUNNING,
                image=QA.IMAGE + "@sha256:" + "b" * 64,
            )
        )
        self.assertIn("RUNTIME_IMAGE_PIN_MISMATCH", plan["reasons"])

    def test_open_outage_requires_review(self) -> None:
        """A known open outage never authorizes a release or boot start."""
        with closing(sqlite3.connect(self.db)) as db:
            db.execute("INSERT INTO outages VALUES(1,NULL)")
            db.commit()
        self.assertIn("OPEN_OUTAGE", self.report()["reasons"])

    def test_missing_database_is_blocked(self) -> None:
        """No automatic restore may replace a missing established DB."""
        self.db.unlink()
        self.assertEqual(self.report()["status"], "BLOCKED")

    def test_corrupt_database_is_blocked(self) -> None:
        """Malformed history is not grounds for a blind stale restore."""
        self.db.write_bytes(b"corrupt")
        self.assertEqual(self.report()["status"], "BLOCKED")

    def test_symlink_state_is_blocked(self) -> None:
        """A linked private directory may not redirect the receipt."""
        alias = self.directory / "linked-state"
        alias.symlink_to(self.state, target_is_directory=True)
        result = QA.evaluate(self.root, alias, self.db, RUNNING)
        self.assertEqual(result["status"], "BLOCKED")

    def test_state_wrong_permissions_is_blocked(self) -> None:
        """Only owner-private 0700 state is acceptable."""
        self.state.chmod(0o755)
        self.assertEqual(self.report()["status"], "BLOCKED")

    def test_receipt_writable_to_other_is_blocked(self) -> None:
        """Unsigned writable deployment metadata cannot establish trust."""
        self.deployed.chmod(0o644)
        self.assertEqual(self.report()["status"], "BLOCKED")

    def test_pending_symlink_is_blocked(self) -> None:
        """Never follow a linked pending marker or remove it."""
        (self.state / "pending-update").symlink_to(self.deployed)
        self.assertEqual(self.report()["status"], "BLOCKED")

    def test_invalid_pending_token_is_blocked(self) -> None:
        """A corrupt pending journal is preserved for manual review."""
        pending = self.state / "pending-update"
        pending.write_text("../../state\n")
        pending.chmod(0o600)
        self.assertEqual(self.report()["status"], "BLOCKED")
        self.assertTrue(pending.exists())

    def test_fixture_schema_rejects_untrusted_fields(self) -> None:
        """Fixtures may not smuggle an 'already verified' signature."""
        payload = dict(RUNNING, signature_verified=True)
        with self.assertRaises(QA.Blocked):
            QA.validate_runtime(payload)

    def test_fixture_is_missing_does_not_probe_live(self) -> None:
        """Invalid QA fixture must never fall back to production Docker."""
        with patch.object(QA, "live_runtime", side_effect=AssertionError):
            code, output = self.invoke(
                "--runtime-fixture",
                str(self.directory / "missing.json"),
                "--jsonl",
            )
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output)["status"], "BLOCKED")

    def test_readonly_runtime_inspection_uses_fixed_container(self) -> None:
        """Only docker inspect with the fixed target is permitted."""
        proc = subprocess.CompletedProcess(
            args=["docker"],
            returncode=0,
            stdout=f"{PIN}|true|healthy|unless-stopped\n",
            stderr="",
        )
        with patch.object(subprocess, "run", return_value=proc) as runner:
            self.assertTrue(QA.live_runtime()["running"])
        arguments = runner.call_args.args[0]
        self.assertEqual(arguments[:2], ["docker", "inspect"])
        self.assertEqual(arguments[-1], QA.CONTAINER)
        self.assertNotIn("docker start", str(arguments))

    def test_missing_docker_inspection_is_blocked(self) -> None:
        """No actual writer is started to compensate for inspect failure."""
        proc = subprocess.CompletedProcess(
            args=["docker"],
            returncode=1,
            stdout="",
            stderr="",
        )
        with (
            patch.object(subprocess, "run", return_value=proc),
            self.assertRaises(QA.Blocked),
        ):
            QA.live_runtime()

    def test_cannot_select_conflicting_modes(self) -> None:
        """A fixture audit may not accidentally inspect real production."""
        with (
            patch.object(QA, "live_runtime", side_effect=AssertionError),
            self.assertRaises(SystemExit),
        ):
            self.invoke(
                "--live-readonly", "--runtime-fixture", str(self.runtime)
            )

    def test_help_is_exposed(self) -> None:
        """Support -h/--help as an ordinary nonmutating CLI."""
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("--live-readonly", proc.stdout)


if __name__ == "__main__":
    unittest.main()
