"""Check that update acceptance requires the expected Docker image."""

import os
import subprocess
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "apply-update.sh"
IMAGE = "ghcr.io/isentinel/shelly-voltage-telegram"
DIGEST = "sha256:" + "a" * 64


class ImageIdentityTests(unittest.TestCase):
    """Reject stale HTTP listeners and unhealthy or unexpected containers."""

    def run_check(self, actual, health="healthy", running="true", http="200"):
        """Use mock Docker and curl commands without contacting a service."""

        script = (
            'source "$1"; '
            "docker() { "
            'if [[ "$1" == "compose" ]]; then echo test-container; '
            'else printf "%s|%s|%s\\n" '
            '"$MOCK_IMAGE" "$MOCK_HEALTH" "$MOCK_RUNNING"; fi; }; '
            'curl() { echo "$MOCK_HTTP"; }; '
            "seq() { echo 1; }; sleep() { :; }; "
            'wait_healthy "$2"'
        )
        env = dict(os.environ)
        env.update(
            MOCK_IMAGE=actual,
            MOCK_HEALTH=health,
            MOCK_RUNNING=running,
            MOCK_HTTP=http,
        )
        return subprocess.run(
            ["bash", "-c", script, "bash", str(SCRIPT), DIGEST],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_correct_signed_image_must_be_running_and_healthy(self):
        """Accept the exact digest only when Docker and HTTP agree."""

        result = self.run_check(f"{IMAGE}@{DIGEST}")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_an_old_healthy_container_cannot_pass(self):
        """Never accept HTTP 200 from a previous image."""

        result = self.run_check(f"{IMAGE}@sha256:{'b' * 64}")
        self.assertNotEqual(result.returncode, 0)

    def test_an_unhealthy_new_container_cannot_pass(self):
        """Refuse a new image that failed Docker's own healthcheck."""

        result = self.run_check(f"{IMAGE}@{DIGEST}", health="unhealthy")
        self.assertNotEqual(result.returncode, 0)

    def test_a_stopped_new_container_cannot_pass(self):
        """Refuse even a matching image unless it is running."""

        result = self.run_check(f"{IMAGE}@{DIGEST}", running="false")
        self.assertNotEqual(result.returncode, 0)

    def test_health_http_503_is_rejected(self):
        """Require HTTP health success in addition to Docker health."""

        result = self.run_check(f"{IMAGE}@{DIGEST}", http="503")
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
