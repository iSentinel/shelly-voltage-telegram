"""Only publish source releases after exact-head CI on the tagged commit."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
CI = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")


class SourceReleaseCIGateTests(unittest.TestCase):
    """Bind source publication and OCI signing to one commit."""

    def test_source_release_requires_reusable_matrix(self) -> None:
        """A tagged source release waits for the complete Python matrix."""
        self.assertIn("  ci:\n    uses: ./.github/workflows/ci.yml", RELEASE)
        self.assertIn("  release:\n    needs: ci", RELEASE)
        self.assertIn('python-version: ["3.11", "3.12", "3.13"]', CI)

    def test_source_release_tests_the_selected_tag(self) -> None:
        """Manual publication cannot verify main and publish another tag."""
        self.assertIn(
            "test_ref: ${{ github.event_name == "
            "'workflow_dispatch' && inputs.tag || github.ref }}",
            RELEASE,
        )
        self.assertIn("ref: ${{ steps.tag.outputs.tag }}", RELEASE)

    def test_source_sha_must_match_ci_sha(self) -> None:
        """A moved tag between matrix and source build must fail closed."""
        self.assertIn(
            "TESTED_SHA: ${{ needs.ci.outputs.tested_commit }}",
            RELEASE,
        )
        self.assertIn('sha="$(git rev-parse HEAD)"', RELEASE)
        self.assertIn('"$sha" == "$TESTED_SHA"', RELEASE)
        self.assertIn("source release differs from CI-tested commit", RELEASE)

    def test_tag_version_and_sha_checks_precede_publication(self) -> None:
        """Never publish an archive that does not match its signed tag."""
        self.assertLess(
            RELEASE.index("Validate tag matches VERSION and CI-tested commit"),
            RELEASE.index("Build reproducible source assets"),
        )
        self.assertLess(
            RELEASE.index("Build reproducible source assets"),
            RELEASE.index("Create GitHub Release"),
        )
        self.assertIn('[[ "$release_version" == "$version" ]]', RELEASE)
        self.assertIn("--verify-tag", RELEASE)

    def test_failed_matrix_cannot_be_bypassed(self) -> None:
        """No failure-tolerant escape from the source publication gate."""
        self.assertNotIn("continue-on-error:", RELEASE)
        self.assertNotIn("always()", RELEASE)


if __name__ == "__main__":
    unittest.main()
