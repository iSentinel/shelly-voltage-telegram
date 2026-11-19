"""Execute the public signed OCI source-release gate without network."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/release.yml").read_text()
BLOCK = WORKFLOW.split(
    "      - name: Check public signed OCI before source publication", 1
)[1].split("      - name: Create GitHub Release", 1)[0]
BODY = BLOCK.split("        run: |\n", 1)[1]
SCRIPT = "\n".join(line[10:] for line in BODY.splitlines()) + "\n"
MOCKS = """python3() {
  case "$1" in
    scripts/qa-public-oci-tag-index.py)
      echo tag >> "$MOCK_TAG_LOG"
      tag_count="$(wc -l < "$MOCK_TAG_LOG")"
      if [ "$MOCK_TAG_EXIT" -ne 0 ]; then return "$MOCK_TAG_EXIT"; fi
      if [ "$tag_count" -ge 2 ] && [ "$MOCK_FINAL_TAG_EXIT" -ne 0 ]; then
        return "$MOCK_FINAL_TAG_EXIT"
      fi
      if [ "$tag_count" -ge 2 ] && [ -n "$MOCK_FINAL_DIGEST" ]; then
        echo "$MOCK_FINAL_DIGEST"
      else
        echo "$MOCK_DIGEST"
      fi
      return 0 ;;
    scripts/verify-published-image.py)
      echo metadata >> "$MOCK_LOG"
      return "$MOCK_METADATA_EXIT" ;;
    *) return 99 ;;
  esac
}
cosign() {
  if [ "$1" = version ]; then printf 'GitVersion: v3.0.5\\n'; return 0; fi
  if [ "$1" = verify ]; then
    echo signature >> "$MOCK_LOG"
    return "$MOCK_SIGNATURE_EXIT"
  fi
  return 99
}
timeout() {
  duration="$1"
  shift
  if [ "$MOCK_TIMEOUT_CMD" = "$1" ]; then
    echo "timeout-$duration" >> "$MOCK_LOG"
    return 124
  fi
  "$@"
}
sleep() { :; }
"""


class SourcePublicOciGateTests(unittest.TestCase):
    """Publishing source requires both public OCI signature and metadata."""

    def setUp(self) -> None:
        """Create a private log and immutable-looking offline identities."""
        temp = tempfile.TemporaryDirectory(prefix="svt-qa-source-oci-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.log = self.root / "calls"

    def run_gate(
        self,
        signature_exit: int = 0,
        metadata_exit: int = 0,
        tag_exit: int = 0,
        digest: str = "sha256:" + "b" * 64,
        ref: str = "refs/tags/v0.5.26-hw",
        commit: str = "a" * 40,
        timeout_cmd: str = "",
        final_digest: str = "",
        final_tag_exit: int = 0,
    ) -> subprocess.CompletedProcess:
        """Run actual workflow shell with all registry commands intercepted."""
        env = dict(
            os.environ,
            GITHUB_REF=ref,
            GITHUB_REPOSITORY="iSentinel/shelly-voltage-telegram",
            SOURCE_TAG="v0.5.26-hw",
            SOURCE_COMMIT=commit,
            MOCK_LOG=str(self.log),
            MOCK_DIGEST=digest,
            MOCK_SIGNATURE_EXIT=str(signature_exit),
            MOCK_METADATA_EXIT=str(metadata_exit),
            MOCK_TAG_EXIT=str(tag_exit),
            MOCK_TIMEOUT_CMD=timeout_cmd,
            MOCK_TAG_LOG=str(self.root / "tags"),
            MOCK_FINAL_DIGEST=final_digest,
            MOCK_FINAL_TAG_EXIT=str(final_tag_exit),
        )
        return subprocess.run(
            ["bash"],
            input=MOCKS + SCRIPT,
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )

    def test_shell_syntax(self) -> None:
        """The extracted GitHub source release gate must parse in Bash."""
        response = subprocess.run(
            ["bash", "-n"],
            input=SCRIPT,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        self.assertEqual(response.returncode, 0, response.stderr)

    def test_public_signature_and_attestations_allow_source_publication(self):
        """Both independent checks are needed to proceed."""
        result = self.run_gate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("SIGNED SOURCE AND PUBLIC OCI PAIR READY", result.stdout)
        self.assertEqual(
            self.log.read_text().splitlines(), ["signature", "metadata"]
        )

    def test_release_tag_stays_pinned_at_publication(self) -> None:
        """Recheck the GHCR tag after proving signed index metadata."""
        result = self.run_gate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (self.root / "tags").read_text().splitlines(), ["tag", "tag"]
        )

    def test_mutated_tag_blocks_after_all_proofs_pass(self) -> None:
        """A replaced OCI index cannot be accepted after verification."""
        result = self.run_gate(final_digest="sha256:" + "c" * 64)
        self.assertEqual(result.returncode, 9, result.stderr)
        self.assertIn("tag changed after signature check", result.stderr)
        self.assertEqual(
            self.log.read_text().splitlines(), ["signature", "metadata"]
        )
        self.assertNotIn("PAIR READY", result.stdout)

    def test_disappearing_tag_blocks_after_signature_pass(self) -> None:
        """A deleted OCI release ref must stop publication immediately."""
        result = self.run_gate(final_tag_exit=3)
        self.assertEqual(result.returncode, 9, result.stderr)
        self.assertIn("tag unavailable after signature check", result.stderr)
        self.assertNotIn("PAIR READY", result.stdout)

    def test_missing_public_signature_never_publishes(self) -> None:
        """After 12 refusals the release must remain blocked."""
        result = self.run_gate(signature_exit=1)
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertIn("source release blocked", result.stderr)
        self.assertEqual(self.log.read_text().splitlines(), ["signature"] * 12)
        self.assertNotIn("PAIR READY", result.stdout)

    def test_bad_manifest_metadata_blocks_even_after_valid_signature(self):
        """A signed digest without matching SPDX/SLSA/commit is not enough."""
        result = self.run_gate(metadata_exit=1)
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(
            self.log.read_text().splitlines(),
            ["signature", "metadata"] * 12,
        )
        self.assertNotIn("PAIR READY", result.stdout)

    def test_timed_out_cosign_verification_blocks_publication(self) -> None:
        """A hung signature lookup must not yield a synthetic release GO."""
        result = self.run_gate(timeout_cmd="cosign")
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(
            self.log.read_text().splitlines(), ["timeout-70"] * 12
        )
        self.assertNotIn("PAIR READY", result.stdout)

    def test_timed_out_oci_lookup_blocks_publication(self) -> None:
        """An unresponsive registry must not fall back to another tag."""
        result = self.run_gate(timeout_cmd="python3")
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(
            self.log.read_text().splitlines(), ["timeout-45"] * 12
        )
        self.assertNotIn("PAIR READY", result.stdout)

    def test_missing_tag_never_falls_back_to_main_or_latest(self) -> None:
        """An unavailable image tag cannot silently inherit another digest."""
        result = self.run_gate(tag_exit=3)
        self.assertEqual(result.returncode, 7)
        self.assertFalse(self.log.exists())

    def test_mutable_digest_not_accepted(self) -> None:
        """The source release must never verify an unpinned image name."""
        result = self.run_gate(digest="latest")
        self.assertEqual(result.returncode, 7)
        self.assertFalse(self.log.exists())

    def test_wrong_tag_or_commit_rejected_before_network(self) -> None:
        """A branch event or wrong commit may not publish tagged source."""
        for values in (
            {"ref": "refs/heads/main"},
            {"commit": "refs/heads/main"},
        ):
            with self.subTest(values=values):
                result = self.run_gate(**values)
                self.assertEqual(result.returncode, 5)
                self.assertFalse(self.log.exists())


if __name__ == "__main__":
    unittest.main()
