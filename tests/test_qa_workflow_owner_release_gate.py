"""Publishing jobs require owner-review controls before any release actions."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / ".github/workflows/release.yml"
IMAGE = ROOT / ".github/workflows/image.yml"
SPEC = importlib.util.spec_from_file_location(
    "svt_release_workflow_gate", ROOT / "scripts/qa-release-tag-protection.py"
)
assert SPEC is not None and SPEC.loader is not None
GUARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GUARD)


class ReleaseWorkflowOwnerGateTests(unittest.TestCase):
    """Inspect only QA git workflow source, never publish to GitHub."""

    def test_source_release_job_needs_reviewed_environment(self) -> None:
        """Source publication must wait for the protected owner environment."""
        self.assertTrue(GUARD.protected_job(SOURCE, "release"))
        text = SOURCE.read_text()
        self.assertIn("    environment: shelly-release-approval", text)

    def test_tagged_oci_requires_reviewed_environment(self) -> None:
        """Main CI images must not be forced through tag release approval."""
        self.assertTrue(GUARD.protected_job(IMAGE, "build"))
        text = IMAGE.read_text()
        self.assertIn(
            f"environment: {GUARD.IMAGE_ENVIRONMENT_EXPR}",
            text,
        )

    def test_oci_prepublish_guard_occurs_before_registry_access(self) -> None:
        """Tag rule checks must precede login, build and push."""
        text = IMAGE.read_text()
        guard = text.index("Verify protected GitHub release controls")
        self.assertLess(guard, text.index("Log in to GHCR"))
        self.assertLess(guard, text.index("Build and push"))
        self.assertIn("if: github.ref_type == 'tag'", text)

    def test_source_guard_precedes_signing_and_release_upload(self) -> None:
        """No source Sigstore signing before source workflow gate passes."""
        text = SOURCE.read_text()
        guard = text.index("Verify protected GitHub release controls")
        self.assertLess(guard, text.index("Sign and verify immutable source"))
        self.assertLess(guard, text.index("Create GitHub Release"))

    def test_both_workflows_fetch_full_tag_ruleset_metadata(self) -> None:
        """List-only GH API response may omit actual ruleset restrictions."""
        for file in (SOURCE, IMAGE):
            with self.subTest(file=file.name):
                text = file.read_text()
                self.assertIn("rulesets?includes_parents=true", text)
                self.assertIn("rulesets/$id", text)
                self.assertIn('jq -s . > "$scratch/rulesets.json"', text)
                self.assertIn("repos/$GITHUB_REPOSITORY/environments", text)

    def test_both_jobs_execute_actual_guard_before_publish(self) -> None:
        """A displayed environment name alone is not a release guard."""
        for file in (SOURCE, IMAGE):
            with self.subTest(file=file.name):
                text = file.read_text()
                self.assertIn("RELEASE_RULESET_READ_TOKEN", text)
                self.assertIn(
                    "python3 scripts/qa-release-tag-protection.py",
                    text,
                )
                for argument in (
                    "--qa-only",
                    "--repo-json",
                    "--rulesets-json",
                    "--environments-json",
                    "--workflows-dir",
                ):
                    self.assertIn(argument, text)

    def test_no_owner_consent_can_be_synthesized_by_workflow(self) -> None:
        """The workflows only audit configuration; they do not approve it."""
        for file in (SOURCE, IMAGE):
            with self.subTest(file=file.name):
                text = file.read_text()
                self.assertNotIn("owner_approval=GRANTED", text)
                self.assertNotIn("release_authorized=true", text)


if __name__ == "__main__":
    unittest.main()
