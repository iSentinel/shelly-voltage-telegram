"""Require exact-head Python CI before Docker image signing and publication."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CI = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
IMAGE = (ROOT / ".github/workflows/image.yml").read_text(encoding="utf-8")


class ImageCISourceGateTests(unittest.TestCase):
    """Verify the static guard contract of the two release workflows."""

    def test_image_build_depends_on_complete_reusable_ci(self) -> None:
        """No tagged image can build if its required CI fails."""
        self.assertRegex(
            IMAGE,
            r"(?m)^  ci:\n    uses: \./\.github/workflows/ci\.yml\n",
        )
        self.assertRegex(IMAGE, r"(?m)^  build:\n    needs: ci\n")

    def test_requested_manual_ref_is_tested(self) -> None:
        """Manual builds must never test main then build another branch."""
        self.assertIn(
            "test_ref: ${{ github.event_name == "
            "'workflow_dispatch' && inputs.ref || github.ref }}",
            IMAGE,
        )
        self.assertIn(
            "ref: ${{ inputs.test_ref || github.ref }}",
            CI,
        )

    def test_source_resolved_to_exact_sha_before_matrix(self) -> None:
        """All Python jobs must check out the same immutable commit."""
        self.assertIn("workflow_call:", CI)
        self.assertIn("value: ${{ jobs.resolve.outputs.sha }}", CI)
        self.assertIn("sha: ${{ steps.resolved.outputs.sha }}", CI)
        self.assertRegex(CI, r"(?m)^  test:\n    needs: resolve\n")
        self.assertIn("ref: ${{ needs.resolve.outputs.sha }}", CI)
        self.assertIn('python-version: ["3.11", "3.12", "3.13"]', CI)

    def test_image_checks_out_exact_ci_sha(self) -> None:
        """The signed image must come from the commit that passed CI."""
        self.assertIn("ref: ${{ needs.ci.outputs.tested_commit }}", IMAGE)
        self.assertIn(
            "TESTED_SHA: ${{ needs.ci.outputs.tested_commit }}", IMAGE
        )
        self.assertIn('"$sha" == "$TESTED_SHA"', IMAGE)
        self.assertIn("image source differs from CI-tested commit", IMAGE)

    def test_signing_still_requires_a_tag_push(self) -> None:
        """A manual image never receives stable-release Cosign signing."""
        self.assertGreaterEqual(
            IMAGE.count(
                "github.event_name == 'push' && "
                "startsWith(github.ref, 'refs/tags/v')"
            ),
            2,
        )
        self.assertIn("python3 scripts/image_release_policy.py", IMAGE)
        self.assertIn("cosign sign --yes", IMAGE)

    def test_no_insecure_always_or_ignore_failure(self) -> None:
        """A failed source-CI gate must never be bypassed."""
        self.assertNotRegex(IMAGE, r"(?m)^\s*continue-on-error:")
        self.assertNotIn("needs.ci.result == 'skipped'", IMAGE)
        self.assertNotIn("needs.ci.result == 'failure'", IMAGE)


if __name__ == "__main__":
    unittest.main()
