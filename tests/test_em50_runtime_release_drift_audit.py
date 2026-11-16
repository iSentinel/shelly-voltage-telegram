"""Tests for the offline, fail-closed runtime release identity checker."""

from __future__ import annotations

import argparse
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "em50-runtime-release-drift-audit.py"
)
SPEC = importlib.util.spec_from_file_location("release_runtime_drift", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
DIGEST = "sha256:" + "a" * 64
IMAGE = MODULE.OFFICIAL + DIGEST
IMAGE_ID = "sha256:" + "b" * 64
APP_SHA = "c" * 64


class ReleaseRuntimeDriftTests(unittest.TestCase):
    """Never access actual Docker or production SQLite."""

    def setUp(self) -> None:
        """Create synthetic release state and responses."""

        self.temp = tempfile.TemporaryDirectory(prefix="release-drift-test-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        (root / "compose.yaml").write_text("services: {}\n")
        self.override = root / "override.yaml"
        self.override.write_text("services: {}\n")
        self.deployed = root / "deployed.json"
        self.deployed.write_text(
            json.dumps({"version": "0.5.21-hw", "digest": DIGEST})
        )
        self.args = argparse.Namespace(
            project_root=root,
            override=self.override,
            yasno_overlay=root / "optional-yasno.yaml",
            deployed=self.deployed,
            container="shelly-voltage-bot",
            expected_app_sha256=APP_SHA,
        )
        self.running_ref = IMAGE
        self.compose_ref = IMAGE
        self.healthy = "healthy"
        self.state = "running"
        self.image_id = IMAGE_ID
        self.local_id = IMAGE_ID
        self.repo_digests = [IMAGE]
        self.actual_sha = APP_SHA

    def fake_output(self, command, cwd=None):
        """Model only bounded, known read-only Docker commands."""

        if command[:2] == ["docker", "inspect"]:
            return (
                f"{self.image_id}|{self.running_ref}|"
                f"{self.state}|{self.healthy}"
            )
        if command[:3] == ["docker", "image", "inspect"]:
            if command[-1] == "{{.Id}}":
                return self.local_id
            return json.dumps(self.repo_digests)
        if command[:2] == ["docker", "exec"]:
            return self.actual_sha + "  /app/app.py"
        if command[:2] == ["docker", "compose"]:
            return json.dumps(
                {
                    "services": {
                        "shelly-voltage": {
                            "image": self.compose_ref,
                            "environment": {
                                "TELEGRAM_BOT_TOKEN": "TEST_SECRET"
                            },
                        }
                    }
                }
            )
        raise AssertionError(f"unexpected command: {command[:2]}")

    def audit(self):
        """Run the real decision code with fake subprocess only."""

        with patch.object(MODULE, "command_output", self.fake_output):
            return MODULE.inspect(self.args)

    def test_exact_pinned_identity_passes_but_never_authorizes(self) -> None:
        """Identity parity is not release signing or owner approval."""

        result = self.audit()
        self.assertEqual(result["status"], "PASS")
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["signature_verified"])
        self.assertFalse(result["offhost_backup_verified"])

    def test_local_emergency_image_is_drift(self) -> None:
        """A local hotfix tag cannot masquerade as a signed image."""

        self.running_ref = "shelly-voltage:issue402-v2-20261116"
        self.compose_ref = self.running_ref
        self.repo_digests = []
        result = self.audit()
        self.assertEqual(result["status"], "DRIFT")
        self.assertIn("unpublished_local_image", result["reason_codes"])
        self.assertIn("updater_pin_mismatch", result["reason_codes"])

    def test_unhealthy_bot_blocks_identity_pass(self) -> None:
        """Healthy Docker is a prerequisite for operational parity."""

        self.healthy = "unhealthy"
        self.assertIn("container_not_healthy", self.audit()["reason_codes"])

    def test_running_image_id_mismatch_is_detected(self) -> None:
        """The image behind the tag must equal the running image."""

        self.local_id = "sha256:" + "d" * 64
        self.assertIn(
            "running_image_id_mismatch", self.audit()["reason_codes"]
        )

    def test_compose_runtime_drift_is_detected(self) -> None:
        """A stale boot pin must never silently pass."""

        self.compose_ref = MODULE.OFFICIAL + "sha256:" + "f" * 64
        self.assertIn(
            "compose_runtime_image_mismatch", self.audit()["reason_codes"]
        )

    def test_running_app_hash_mismatch_is_detected(self) -> None:
        """The actually executed Python source must match evidence."""

        self.actual_sha = "e" * 64
        self.assertIn(
            "runtime_source_hash_mismatch", self.audit()["reason_codes"]
        )

    def test_missing_registry_digest_is_detected(self) -> None:
        """An unverified immutable reference must not pass."""

        self.repo_digests = []
        self.assertIn(
            "registry_digest_not_verified", self.audit()["reason_codes"]
        )

    def test_missing_updater_override_is_detected(self) -> None:
        """A missing boot-time override is an explicit release risk."""

        self.override.unlink()
        self.assertIn("updater_override_missing", self.audit()["reason_codes"])

    def test_ignores_environment_secrets_from_compose(self) -> None:
        """Never leak parsed Compose credentials in the report."""

        self.assertNotIn("TEST_SECRET", json.dumps(self.audit()))

    def test_corrupt_deployed_metadata_fails(self) -> None:
        """Unknown deployment format is not a valid release."""

        self.deployed.write_text('{"digest": "invalid", "version": "x"}')
        with self.assertRaises(ValueError):
            self.audit()

    def test_invalid_container_name_fails_before_docker(self) -> None:
        """Disallow argument smuggling into the Docker command."""

        self.args.container = "--privileged"
        with self.assertRaises(ValueError):
            self.audit()

    def test_help_can_be_parsed(self) -> None:
        """The standalone CLI has a documented help entry point."""

        source = SCRIPT.read_text()
        self.assertIn('parser.add_argument("--jsonl"', source)
        self.assertIn("argparse.ArgumentParser", source)


if __name__ == "__main__":
    unittest.main()
