"""Exercise independent published OCI certificate binding offline."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/verify-published-image.yml").read_text()
BLOCK = WORKFLOW.split("      - name: Verify immutable image signature", 1)[1]
BLOCK = BLOCK.split("      - name:", 1)[0].split("        run: |\n", 1)[1]
SCRIPT = "\n".join(line[10:] for line in BLOCK.splitlines())
REPO = "iSentinel/shelly-voltage-telegram"
TAG = "v0.5.28-hw"
COMMIT = "b" * 40
DIGEST = "sha256:" + "a" * 64
MOCK = """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
Path(os.environ["MOCK_LOG"]).write_text(json.dumps(args))
if os.environ["MOCK_REJECT"] == "1":
    sys.exit(9)
print('[{}]')
"""


class IndependentSignatureGateTests(unittest.TestCase):
    """Inspect the real shell arguments using an offline mock CLI."""

    def setUp(self):
        """Create isolated mock executable and output directory."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.log = self.root / "calls.json"
        cosign = self.root / "cosign"
        cosign.write_text(MOCK)
        cosign.chmod(0o700)

    def execute(self, reject=False, commit=COMMIT):
        """Run only the independent signature shell block."""
        env = dict(
            os.environ,
            PATH=str(self.root) + os.pathsep + os.environ["PATH"],
            RUNNER_TEMP=str(self.root),
            MOCK_LOG=str(self.log),
            MOCK_REJECT=str(int(reject)),
            RELEASE_VERSION=TAG,
            RELEASE_DIGEST=DIGEST,
            RELEASE_COMMIT=commit,
        )
        return subprocess.run(
            ["bash", "-c", SCRIPT],
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )

    def test_exact_certificate_and_digest(self):
        """Bind identity, issuer, source commit, repository, tag and digest."""
        result = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(self.log.read_text()),
            [
                "verify",
                "--certificate-identity",
                f"https://github.com/{REPO}/.github/workflows/"
                f"image.yml@refs/tags/{TAG}",
                "--certificate-oidc-issuer",
                "https://token.actions.githubusercontent.com",
                "--certificate-github-workflow-sha",
                COMMIT,
                "--certificate-github-workflow-ref",
                f"refs/tags/{TAG}",
                "--certificate-github-workflow-repository",
                REPO,
                f"ghcr.io/{REPO.lower()}@{DIGEST}",
            ],
        )

    def test_certificate_refusal_blocks(self):
        """A failed certificate check never prints success."""
        result = self.execute(reject=True)
        self.assertEqual(result.returncode, 9)
        self.assertNotIn("SIGNATURE: PASS", result.stdout)

    def test_bad_commit_blocks_before_cosign(self):
        """A malformed commit cannot reach signature verification."""
        result = self.execute(commit="main")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.log.exists())
