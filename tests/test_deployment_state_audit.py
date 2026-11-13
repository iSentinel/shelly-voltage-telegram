"""Test fail-closed parity between local source and signed OCI deployment."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts/deployment-state-audit.py"
)
SPEC = importlib.util.spec_from_file_location("state_audit", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)
DIGEST = "sha256:" + "a" * 64
IMAGE = f"ghcr.io/isentinel/shelly-voltage-telegram@{DIGEST}"


class DeploymentStateTests(unittest.TestCase):
    """Use protected fixture files and mock Docker's read-only response."""

    def setUp(self) -> None:
        """Create a coherent signed-deployment fixture in private scratch."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "source"
        self.state = Path(tmp.name) / "state"
        self.root.mkdir()
        self.state.mkdir()
        self.write_version("0.5.18-hw")
        self.write_marker("0.5.18-hw", DIGEST)
        self.write_override(IMAGE)

    def write_version(self, version: str) -> None:
        """Set both release markers."""
        for name in ("VERSION", "RELEASE_VERSION"):
            (self.root / name).write_text(version + "\n", encoding="ascii")

    def write_marker(self, version: str, digest: str) -> None:
        """Simulate a root-private pinned image receipt."""
        path = self.state / "deployed.json"
        path.write_text(json.dumps({"version": version, "digest": digest}))
        path.chmod(0o600)

    def write_override(self, image: str) -> None:
        """Write a minimal private Compose image override."""
        path = self.state / "override.yaml"
        path.write_text(
            f"services:\n  shelly-voltage:\n    image: {image}\n",
            encoding="utf-8",
        )
        path.chmod(0o600)

    def audit(self, image: str = IMAGE, health: str = "healthy") -> dict:
        """Return a real audit using mocked read-only Docker inspection."""
        payload = f"{image}|{health}|unless-stopped\n"
        result = subprocess.CompletedProcess([], 0, payload, "")
        with patch.object(
            AUDIT.subprocess, "run", return_value=result
        ) as call:
            report = AUDIT.audit(self.root, self.state)
        call.assert_called_once()
        self.assertEqual(call.call_args.args[0][0:2], ["docker", "inspect"])
        return report

    def test_matching_source_and_image_is_pass_only(self) -> None:
        """A matching runtime audit never grants deployment approval."""
        report = self.audit()
        self.assertEqual(report["status"], "PASS")
        self.assertFalse(report["production_modified"])
        self.assertFalse(report["signature_verified_here"])
        self.assertFalse(report["rollout_authorized"])

    def test_actual_production_version_split_is_blocked(self) -> None:
        """A v0.5.15 source cannot silently accompany the v0.5.18 image."""
        self.write_version("0.5.15-hw")
        report = self.audit()
        self.assertEqual(report["status"], "BLOCKED")
        self.assertIn("SOURCE_IMAGE_VERSION_MISMATCH", report["reason_codes"])

    def test_compose_override_digest_drift_is_blocked(self) -> None:
        """A mutable override cannot silently reference another image."""
        self.write_override(
            "ghcr.io/isentinel/shelly-voltage-telegram@" + "sha256:" + "b" * 64
        )
        self.assertIn("IMAGE_OVERRIDE_MISMATCH", self.audit()["reason_codes"])

    def test_running_image_digest_drift_is_blocked(self) -> None:
        """Only the pinned image digest can be accepted."""
        self.assertIn(
            "RUNNING_IMAGE_MISMATCH",
            self.audit(image=IMAGE[:-1] + "b")["reason_codes"],
        )

    def test_unhealthy_container_is_blocked(self) -> None:
        """A matching image with a failed health status is not ready."""
        self.assertIn(
            "RUNTIME_NOT_HEALTHY",
            self.audit(health="unhealthy")["reason_codes"],
        )

    def test_pending_update_is_blocked(self) -> None:
        """Pending interrupted updates make parity insufficient."""
        (self.state / "pending-update").write_text("interrupted")
        self.assertIn("UPDATE_PENDING", self.audit()["reason_codes"])

    def test_mismatched_source_markers_are_blocked(self) -> None:
        """A candidate marker must not disagree with release metadata."""
        (self.root / "RELEASE_VERSION").write_text("0.5.17-hw\n")
        self.assertEqual(
            AUDIT.audit(self.root, self.state)["reason_codes"],
            ["SOURCE_MARKERS_INCONSISTENT"],
        )

    def test_linked_deployed_receipt_is_blocked(self) -> None:
        """A linked release marker is unsafe for root-owned acceptance."""
        path = self.state / "deployed.json"
        content = self.state / "other.json"
        path.rename(content)
        path.symlink_to(content)
        self.assertEqual(
            AUDIT.audit(self.root, self.state)["reason_codes"],
            ["LOCAL_FILE_LINKED"],
        )

    def test_world_readable_receipt_is_blocked(self) -> None:
        """A public writable or readable receipt is not trusted."""
        (self.state / "deployed.json").chmod(0o644)
        self.assertEqual(
            AUDIT.audit(self.root, self.state)["reason_codes"],
            ["PRIVATE_STATE_UNSAFE"],
        )

    def test_dangling_pending_marker_is_blocked(self) -> None:
        """A symlinked interrupted-update marker is not absence."""
        (self.state / "pending-update").symlink_to(self.state / "gone")
        self.assertIn("UPDATE_PENDING", self.audit()["reason_codes"])

    def test_disabled_restart_policy_is_blocked(self) -> None:
        """A healthy container without restart recovery is not ready."""
        result = subprocess.CompletedProcess(
            [], 0, f"{IMAGE}|healthy|no\\n", ""
        )
        with patch.object(AUDIT.subprocess, "run", return_value=result):
            output = AUDIT.audit(self.root, self.state)
        self.assertIn("RUNTIME_NOT_HEALTHY", output["reason_codes"])

    def test_inspect_fail_closed(self) -> None:
        """Unavailable Docker metadata cannot satisfy parity."""
        failure = subprocess.CompletedProcess([], 1, "", "not found")
        with patch.object(AUDIT.subprocess, "run", return_value=failure):
            result = AUDIT.audit(self.root, self.state)
        self.assertEqual(result["reason_codes"], ["DOCKER_INSPECT_FAILED"])


if __name__ == "__main__":
    unittest.main()
