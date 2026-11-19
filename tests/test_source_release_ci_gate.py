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

    def test_source_blob_requires_pinned_keyless_cosign(self) -> None:
        """Sign source bytes with a pinned GitHub OIDC identity."""
        self.assertIn("id-token: write", RELEASE)
        self.assertIn(
            "sigstore/cosign-installer@"
            "6f9f17788090df1f26f669e9d70d6ae9567deba6",
            RELEASE,
        )
        self.assertIn("cosign sign-blob --yes --bundle", RELEASE)
        self.assertIn("cosign verify-blob --bundle", RELEASE)
        self.assertIn("--certificate-identity", RELEASE)
        self.assertIn("--certificate-oidc-issuer", RELEASE)
        self.assertIn("https://token.actions.githubusercontent.com", RELEASE)
        self.assertIn("release.yml@refs/tags/$SOURCE_TAG", RELEASE)

    def test_manual_source_signing_requires_actual_tag_ref(self) -> None:
        """Dispatching from a branch may not impersonate a signed tag."""
        self.assertIn('"$GITHUB_REF" == "refs/tags/$tag"', RELEASE)
        self.assertIn('"$GITHUB_REF" == "refs/tags/$SOURCE_TAG"', RELEASE)
        self.assertIn(
            "source signing requires the exact tag event ref", RELEASE
        )

    def test_release_publishes_exact_blob_and_bundle(self) -> None:
        """Attach the cryptographic bundle for the exact source asset."""
        self.assertIn(
            'archive="dist/shelly-voltage-telegram-$version.tar.gz"',
            RELEASE,
        )
        self.assertIn('"$archive" "$archive.sigstore.json"', RELEASE)
        self.assertNotIn("dist/shelly-voltage-telegram-*.tar.gz", RELEASE)

    def test_source_signing_before_public_release(self) -> None:
        """A failed signature verification must stop publication."""
        self.assertLess(
            RELEASE.index("Build reproducible source assets"),
            RELEASE.index("Sign and verify immutable source bundle"),
        )
        self.assertLess(
            RELEASE.index("Sign and verify immutable source bundle"),
            RELEASE.index("Create GitHub Release"),
        )
        self.assertNotIn("--insecure-ignore-tlog", RELEASE)
        self.assertNotIn(
            "|| true",
            RELEASE.split(
                "      - name: Sign and verify immutable source bundle"
            )[1].split("      - name: Create GitHub Release")[0],
        )

    def test_failed_matrix_cannot_be_bypassed(self) -> None:
        """No failure-tolerant escape from the source publication gate."""
        self.assertNotIn("continue-on-error:", RELEASE)
        self.assertNotIn("always()", RELEASE)


if __name__ == "__main__":
    unittest.main()
