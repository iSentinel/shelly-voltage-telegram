"""Test that dry-run cleanup does not corrupt updater exit status."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "apply-update.sh"


class UpdaterWorkspaceTests(unittest.TestCase):
    """Validate temp cleanup after an update check or error."""

    def run_dry_run(self, fail_archive=False):
        """Execute the shell flow with network and hardware checks mocked."""

        with tempfile.TemporaryDirectory() as directory:
            script = (
                'source "$1"; '
                "requirements() { :; }; preflight() { :; }; "
                "verify_image() { :; }; "
                "python3() { "
                'if [[ "$*" == *" digest "* ]]; then echo "sha256:abc"; fi; '
                "}; "
                "verify_source_archive() { "
                f"return {1 if fail_archive else 0}; "
                "}; "
                "main --version 0.5.12-hw --dry-run"
            )
            env = dict(os.environ, TMPDIR=directory)
            result = subprocess.run(
                ["bash", "-c", script, "bash", str(SCRIPT)],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            leftovers = list(Path(directory).iterdir())
        return result, leftovers

    def test_successful_dry_run_exits_zero_and_cleans(self):
        """A successful check must not fail during the exit trap."""

        result, leftovers = self.run_dry_run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DRY-RUN PASS", result.stdout)
        self.assertEqual(leftovers, [])

    def test_failed_verification_cleans_without_success_message(self):
        """Failed source verification must keep a failure exit code."""

        result, leftovers = self.run_dry_run(fail_archive=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("DRY-RUN PASS", result.stdout)
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
