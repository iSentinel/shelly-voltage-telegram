"""Run anonymous image-signature release shell policy without network."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGE = (ROOT / ".github/workflows/image.yml").read_text(encoding="utf-8")
STEP = IMAGE.split(
    "      - name: Verify published release signature anonymously", 1
)[1]
BODY = STEP.split("        run: |\n", 1)[1]
SCRIPT = "\n".join(line[10:] for line in BODY.splitlines()) + "\n"


class PublicImageSignatureGateTests(unittest.TestCase):
    """Ensure publication fails after bounded mock verification failures."""

    def setUp(self) -> None:
        """Create a private fake Cosign that cannot access any registry."""
        temp = tempfile.TemporaryDirectory(prefix="svt-qa-cosign-policy-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.log = self.root / "calls"
        executable = self.root / "cosign"
        executable.write_text(
            "#!/bin/sh\n"
            'if [ "$1" = version ]; then\n'
            "  printf 'GitVersion: v3.0.5\\n'\n"
            "  exit 0\n"
            "fi\n"
            'if [ "$1" = verify ]; then\n'
            "  printf 'verify\\n' >> \"$MOCK_LOG\"\n"
            '  count="$(wc -l < "$MOCK_LOG")"\n'
            '  if [ "$MOCK_SUCCESS_AFTER" -gt 0 ] && '
            '[ "$count" -ge "$MOCK_SUCCESS_AFTER" ]; then\n'
            "    exit 0\n"
            "  fi\n"
            '  exit "$MOCK_VERIFY_EXIT"\n'
            "fi\n"
            "exit 99\n",
            encoding="utf-8",
        )
        executable.chmod(0o700)

    def run_gate(
        self,
        rc: int,
        ref: str = "refs/tags/v0.5.26-hw",
        success_after: int = 0,
    ):
        """Run the actual workflow Bash block with mocked Cosign and sleep."""
        env = dict(
            os.environ,
            PATH=str(self.root) + ":" + os.environ.get("PATH", ""),
            MOCK_LOG=str(self.log),
            MOCK_VERIFY_EXIT=str(rc),
            MOCK_SUCCESS_AFTER=str(success_after),
            GITHUB_REPOSITORY="iSentinel/shelly-voltage-telegram",
            GITHUB_REF=ref,
            IMAGE_REPOSITORY="iSentinel/shelly-voltage-telegram",
            IMAGE_DIGEST="sha256:" + "a" * 64,
            IMAGE_COMMIT="b" * 40,
        )
        return subprocess.run(
            ["bash"],
            input="sleep() { :; }\n" + SCRIPT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )

    def test_shell_is_syntactically_valid(self) -> None:
        """The extracted run block must parse as executable Bash."""
        result = subprocess.run(
            ["bash", "-n"],
            input=SCRIPT,
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_three_failed_public_verifications_block_release(self) -> None:
        """No transient retry loop may turn three failures into PASS."""
        result = self.run_gate(3)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("public OCI signature not verifiable", result.stderr)
        self.assertEqual(self.log.read_text().splitlines(), ["verify"] * 3)
        self.assertNotIn("VERIFICATION: PASS", result.stdout)

    def test_first_success_needs_only_one_lookup(self) -> None:
        """A verifiable public signature does not trigger extra retries."""
        result = self.run_gate(0)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("VERIFICATION: PASS", result.stdout)
        self.assertEqual(self.log.read_text().splitlines(), ["verify"])

    def test_transient_first_failure_then_success(self) -> None:
        """A bounded retry permits propagation without ignoring failure."""
        result = self.run_gate(3, success_after=2)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("VERIFICATION: PASS", result.stdout)
        self.assertEqual(self.log.read_text().splitlines(), ["verify"] * 2)

    def test_branch_event_ref_never_runs_verification(self) -> None:
        """A mutable branch ref cannot impersonate a signed release."""
        result = self.run_gate(0, ref="refs/heads/main")
        self.assertEqual(result.returncode, 6)
        self.assertFalse(self.log.exists())


if __name__ == "__main__":
    unittest.main()
