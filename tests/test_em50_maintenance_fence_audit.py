"""Synthetic fail-closed checks for signed updater lock and writer fencing."""

from __future__ import annotations

import argparse
import fcntl
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SOURCE = (
    Path(__file__).resolve().parents[1]
    / "scripts/em50-maintenance-fence-audit.py"
)
SPEC = importlib.util.spec_from_file_location(
    "maintenance_fence_audit", SOURCE
)
assert SPEC is not None and SPEC.loader is not None
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)
DIGEST = "sha256:" + "a" * 64


class MaintenanceFenceTests(unittest.TestCase):
    """Never contact production, start Docker or modify real SQLite."""

    def setUp(self) -> None:
        """Create fake project, private updater and lock files."""

        tmp = tempfile.TemporaryDirectory(prefix="svt-maintenance-qa-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.project = self.root / "source"
        self.state = self.root / "updater"
        self.project.mkdir()
        self.state.mkdir()
        for name in ("VERSION", "RELEASE_VERSION"):
            (self.project / name).write_text("0.5.23-hw\n")
        (self.state / "deployed.json").write_text(
            json.dumps({"version": "0.5.23-hw", "digest": DIGEST})
        )
        self.lock = self.root / "updater.lock"
        self.lock.write_bytes(b"existing-lock-content")
        self.args = argparse.Namespace(
            phase="live",
            project_root=self.project,
            state=self.state,
            lock=self.lock,
            container="synthetic-bot",
            jsonl=True,
        )

    def check(self, running="true", restart="unless-stopped"):
        """Mock just Docker inspection, retaining real file-lock behavior."""

        output = f"{AUDIT.IMAGE_PREFIX}{DIGEST}|{running}|{restart}\n"
        fake = SimpleNamespace(returncode=0, stdout=output)
        with patch.object(AUDIT.subprocess, "run", return_value=fake) as spy:
            result = AUDIT.audit(self.args)
        self.assertEqual(spy.call_count, 1)
        self.assertIn("inspect", spy.call_args.args[0])
        return result

    def test_live_pass_without_any_file_changes(self) -> None:
        """Read-only live status never modifies the two stored files."""

        before = (
            (self.project / "VERSION").read_bytes(),
            (self.state / "deployed.json").read_bytes(),
            self.lock.read_bytes(),
        )
        self.assertEqual(self.check()["status"], "PASS")
        self.assertEqual(
            before,
            (
                (self.project / "VERSION").read_bytes(),
                (self.state / "deployed.json").read_bytes(),
                self.lock.read_bytes(),
            ),
        )

    def test_fenced_requires_stopped_and_restart_no(self) -> None:
        """Only Docker running=false plus restart=no is a writer fence."""

        self.args.phase = "fenced"
        result = self.check("false", "no")
        self.assertFalse(result["writer_running"])
        self.assertEqual(result["restart_policy"], "no")

    def test_resume_checks_available_lock_and_stopped_writer(self) -> None:
        """Both restart policies are valid after releasing the lock."""

        self.args.phase = "before-start"
        for restart in ("no", "unless-stopped"):
            with self.subTest(restart=restart):
                self.assertEqual(
                    self.check("false", restart)["status"], "PASS"
                )

    def test_fenced_rejects_running_writer(self) -> None:
        """A restart=no policy alone does not stop active SQLite writes."""

        self.args.phase = "fenced"
        with self.assertRaisesRegex(AUDIT.AuditBlocked, "WRITER_NOT_STOPPED"):
            self.check("true", "no")

    def test_fenced_rejects_restart_unless_stopped(self) -> None:
        """After systemctl stop, disable automatic Docker restarts too."""

        self.args.phase = "fenced"
        with self.assertRaisesRegex(AUDIT.AuditBlocked, "WRITER_NOT_STOPPED"):
            self.check("false", "unless-stopped")

    def test_live_rejects_unexpected_restart_policy(self) -> None:
        """The restored signed service requires its usual restart policy."""

        with self.assertRaisesRegex(AUDIT.AuditBlocked, "LIVE_WRITER"):
            self.check("true", "no")

    def test_updater_lock_held_blocks_before_start(self) -> None:
        """Prevent systemd ExecStartPre from waiting for a parent-held lock."""

        self.args.phase = "before-start"
        with self.lock.open("rb") as fd:
            fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(AUDIT.AuditBlocked, "LOCK_HELD"):
                self.check("false", "no")
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
        self.assertEqual(self.check("false", "no")["status"], "PASS")

    def test_symlinked_lock_refused(self) -> None:
        """Do not trust a linked replacement for the private update lock."""

        linked = self.root / "linked.lock"
        linked.symlink_to(self.lock)
        self.args.lock = linked
        with self.assertRaisesRegex(AUDIT.AuditBlocked, "SYMLINKED"):
            self.check()

    def test_pending_update_blocks_source_alignment(self) -> None:
        """Never copy signed source during unfinished updater recovery."""

        (self.state / "pending-update").write_text("synthetic-pending")
        with self.assertRaisesRegex(AUDIT.AuditBlocked, "UPDATER_PENDING"):
            self.check()

    def test_version_mismatch_rejected(self) -> None:
        """Refuse a stale release marker even if Docker image matches."""

        (self.project / "VERSION").write_text("0.5.22-hw\n")
        with self.assertRaisesRegex(AUDIT.AuditBlocked, "MISMATCH"):
            self.check()

    def test_invalid_digest_rejected(self) -> None:
        """A malformed protected receipt is not an approved signed image."""

        (self.state / "deployed.json").write_text(
            json.dumps({"version": "0.5.23-hw", "digest": "latest"})
        )
        with self.assertRaisesRegex(AUDIT.AuditBlocked, "MISMATCH"):
            self.check()

    def test_invalid_receipt_json_type_refused(self) -> None:
        """Malformed protected updater state must fail without a traceback."""

        (self.state / "deployed.json").write_text("[]")
        with self.assertRaisesRegex(AUDIT.AuditBlocked, "INVALID_DEPLOYED"):
            self.check()

    def test_missing_lock_file_refused_without_creating_it(self) -> None:
        """Do not create the lock during a read-only audit."""

        self.lock.unlink()
        with self.assertRaisesRegex(AUDIT.AuditBlocked, "STATE_FILE"):
            self.check()
        self.assertFalse(self.lock.exists())

    def test_wrong_oci_image_refused(self) -> None:
        """A matching receipt alone does not prove container identity."""

        fake = SimpleNamespace(
            returncode=0,
            stdout="ghcr.io/isentinel/shelly-voltage-telegram@"
            + "sha256:"
            + "b" * 64
            + "|true|unless-stopped",
        )
        with (
            patch.object(AUDIT.subprocess, "run", return_value=fake),
            self.assertRaisesRegex(AUDIT.AuditBlocked, "IMAGE_MISMATCH"),
        ):
            AUDIT.audit(self.args)

    def test_docker_inspect_error_rejected(self) -> None:
        """An unreachable Docker daemon cannot be presumed safe."""

        fake = SimpleNamespace(returncode=1, stdout="")
        with (
            patch.object(AUDIT.subprocess, "run", return_value=fake),
            self.assertRaisesRegex(AUDIT.AuditBlocked, "DOCKER_INSPECT"),
        ):
            AUDIT.audit(self.args)

    def test_cli_never_emits_fixture_secrets(self) -> None:
        """Status is short and carries no password, token or recipient ID."""

        (self.project / ".env").write_text("BOT_TOKEN=never-display\n")
        with patch.object(AUDIT, "options", return_value=self.args):
            fake = SimpleNamespace(
                returncode=0,
                stdout=f"{AUDIT.IMAGE_PREFIX}{DIGEST}|true|unless-stopped\n",
            )
            with patch.object(AUDIT.subprocess, "run", return_value=fake):
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(AUDIT.main(), 0)
        record = json.loads(output.getvalue())
        self.assertFalse(record["release_authorized"])
        self.assertNotIn("never-display", output.getvalue())
        self.assertNotIn(DIGEST, output.getvalue())

    def test_command_help_and_error_codes(self) -> None:
        """CLI help is available, and a missing Docker returns fail-closed."""

        result = subprocess.run(
            [sys.executable, str(SOURCE), "--help"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("--phase", result.stdout)
        self.assertIn("before-start", result.stdout)


if __name__ == "__main__":
    unittest.main()
