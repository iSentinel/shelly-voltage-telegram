"""Exercise protected source upgrades without Docker writes."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "scripts/installer-signed-image-guard.py"
SPEC = importlib.util.spec_from_file_location("signed_guard", GUARD)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
DIGEST = "sha256:" + "a" * 64
IMAGE = f"ghcr.io/isentinel/shelly-voltage-telegram@{DIGEST}"


class SignedSourceInstallTests(unittest.TestCase):
    """Use disposable root-owned fixture state and read-only Docker mocks."""

    def setUp(self) -> None:
        """Build a valid protected signed-image receipt."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / "source"
        self.state = Path(self.temp.name) / "state"
        self.source.mkdir()
        self.state.mkdir(mode=0o700)
        for name in ("VERSION", "RELEASE_VERSION"):
            (self.source / name).write_text("0.5.19-hw\n")
        self.marker = self.state / "deployed.json"
        self.override = self.state / "override.yaml"
        self.marker.write_text(
            json.dumps({"version": "0.5.19-hw", "digest": DIGEST})
        )
        self.marker.chmod(0o600)
        self.override.write_text(
            f"services:\n  shelly-voltage:\n    image: {IMAGE}\n"
        )
        self.override.chmod(0o600)

    def evaluate(
        self,
        docker_image: str = IMAGE,
        running: str = "false",
        restart: str = "no",
        dry_run: bool = False,
    ) -> dict:
        """Call the guard with a bounded fake Docker inspect response."""
        result = subprocess.CompletedProcess(
            [], 0, f"{docker_image}|{running}|{restart}\n", ""
        )
        with patch.object(
            MODULE.subprocess, "run", return_value=result
        ) as spy:
            decision = MODULE.inspect(self.source, self.state, dry_run)
        spy.assert_called_once()
        self.assertEqual(spy.call_args.args[0][:2], ["docker", "inspect"])
        return decision

    def blocked(self, expected: str, **kwargs: str) -> None:
        """Check the exact fail-closed reason code."""
        with self.assertRaises(MODULE.GuardBlocked) as error:
            self.evaluate(**kwargs)
        self.assertEqual(str(error.exception), expected)

    def test_stopped_fenced_signed_image_is_accepted(self) -> None:
        """Matching immutable image and source can be installed safely."""
        report = self.evaluate()
        self.assertEqual(report["status"], "PASS")
        self.assertTrue(report["stopped_and_fenced"])
        self.assertFalse(report["rollout_authorized"])
        self.assertFalse(report["signature_verified_here"])

    def test_readonly_dry_run_allows_running_image(self) -> None:
        """A planning command never demands stopping production first."""
        report = self.evaluate(
            running="true", restart="unless-stopped", dry_run=True
        )
        self.assertEqual(report["status"], "PASS")
        self.assertFalse(report["stopped_and_fenced"])

    def test_version_mismatch_is_blocked_before_docker(self) -> None:
        """Never replace source for the wrong signed release."""
        (self.source / "VERSION").write_text("0.5.18-hw\n")
        self.blocked("SOURCE_MARKERS_INCONSISTENT")

    def test_coherent_but_old_source_is_blocked(self) -> None:
        """Signed v0.5.19 image cannot be paired with old source."""
        for name in ("VERSION", "RELEASE_VERSION"):
            (self.source / name).write_text("0.5.18-hw\n")
        self.blocked("SOURCE_SIGNED_VERSION_MISMATCH")

    def test_wrong_override_is_blocked(self) -> None:
        """An override to a different digest must never be used."""
        self.override.write_text(
            "services:\n  shelly-voltage:\n    image: " + IMAGE[:-1] + "b\n"
        )
        self.blocked("IMAGE_OVERRIDE_MISMATCH")

    def test_signed_writer_must_be_stopped_and_fenced(self) -> None:
        """Both running and restartable containers are prohibited."""
        self.blocked("WRITER_NOT_STOPPED_AND_FENCED", running="true")
        self.blocked("WRITER_NOT_STOPPED_AND_FENCED", restart="unless-stopped")

    def test_forged_running_image_is_blocked(self) -> None:
        """A matching marker does not excuse a different actual image."""
        self.blocked("RUNNING_IMAGE_MISMATCH", docker_image=IMAGE[:-1] + "b")

    def test_pending_update_is_blocked(self) -> None:
        """Interruptions are explicitly denied before source changes."""
        (self.state / "pending-update").write_text("interrupted")
        self.blocked("UPDATE_PENDING")

    def test_symlinked_state_is_blocked(self) -> None:
        """Linked protected release receipts cannot be trusted."""
        other = self.state / "other.json"
        self.marker.rename(other)
        self.marker.symlink_to(other)
        self.blocked("LOCAL_FILE_LINKED")

    def test_missing_signed_override_is_blocked(self) -> None:
        """Missing signed override cannot turn into an unsigned build."""
        self.override.unlink()
        self.blocked("SIGNED_STATE_UNAVAILABLE")

    def test_insecure_private_state_directory_is_blocked(self) -> None:
        """The installed receipt directory itself must remain private."""
        self.state.chmod(0o755)
        self.blocked("SIGNED_STATE_DIRECTORY_UNSAFE")

    def test_public_receipt_is_blocked(self) -> None:
        """Protected marker permissions are part of release identity."""
        self.marker.chmod(0o644)
        self.blocked("PRIVATE_STATE_UNSAFE")


if __name__ == "__main__":
    unittest.main()
