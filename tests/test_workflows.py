import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


class WorkflowSecurityTests(unittest.TestCase):
    def test_permissions_are_explicit(self):
        for path in WORKFLOWS.glob("*.yml"):
            text = path.read_text(encoding="utf-8")
            self.assertIn("permissions:", text, path.name)

    def test_ci_enforces_maintainability_rules(self):
        """CI must keep the project quality rules enabled."""

        text = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("ruff format --line-length 79 --check", text)
        self.assertIn(
            "ruff check --line-length 79 --select E,F,I,UP,SIM", text
        )
        self.assertIn("python scripts/check-quality.py", text)

    def test_no_pull_request_target(self):
        for path in WORKFLOWS.glob("*.yml"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("pull_request_target:", text, path.name)

    def test_image_has_sbom_and_provenance(self):
        text = (WORKFLOWS / "image.yml").read_text(encoding="utf-8")
        self.assertIn("sbom: true", text)
        self.assertIn("provenance: mode=max", text)

    def test_image_dispatch_supports_exact_ref_and_tag(self):
        text = (WORKFLOWS / "image.yml").read_text(encoding="utf-8")
        self.assertIn(
            'description: "Existing branch, tag or commit to build"', text
        )
        self.assertIn(
            "ref: ${{ github.event_name == 'workflow_dispatch'", text
        )
        self.assertIn("type=raw,value=${{ inputs.image_tag }}", text)
        self.assertIn("explicit image tag must start with v", text)

    def test_image_dispatch_uses_checked_out_source_metadata(self):
        text = (WORKFLOWS / "image.yml").read_text(encoding="utf-8")
        self.assertIn('sha="$(git rev-parse HEAD)"', text)
        revision_label = (
            "org.opencontainers.image.revision=${{ steps.source.outputs.sha }}"
        )
        version_label = (
            "org.opencontainers.image.version="
            "${{ steps.source.outputs.version }}"
        )
        self.assertIn(revision_label, text)
        self.assertIn(version_label, text)
        self.assertIn(
            "annotations: ${{ steps.meta.outputs.annotations }}", text
        )
        self.assertIn(
            "enable=${{ github.event_name != 'workflow_dispatch' }}", text
        )
        self.assertIn("manual-${{ steps.source.outputs.short_sha }}", text)

    def test_release_images_are_keyless_signed_by_digest(self):
        """Versioned GHCR images must be signed with Sigstore Cosign."""
        text = (WORKFLOWS / "image.yml").read_text(encoding="utf-8")
        self.assertIn("id-token: write", text)
        self.assertIn(
            "sigstore/cosign-installer@"
            "6f9f17788090df1f26f669e9d70d6ae9567deba6",
            text,
        )
        self.assertIn("id: build", text)
        self.assertIn("IMAGE_DIGEST: ${{ steps.build.outputs.digest }}", text)
        self.assertIn("IMAGE_REPOSITORY: ${{ github.repository }}", text)
        self.assertIn('image_name="ghcr.io/${IMAGE_REPOSITORY,,}"', text)
        self.assertIn(
            'cosign sign --yes "${image_name}@${IMAGE_DIGEST}"', text
        )
        self.assertIn("startsWith(inputs.image_tag, 'v')", text)

    def test_release_uses_exact_tag_and_checksum(self):
        text = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
        self.assertIn("ref: ${{ steps.tag.outputs.tag }}", text)
        self.assertIn("sha256sum -c SHA256SUMS", text)
        self.assertIn("--verify-tag", text)

    def test_release_rejects_operational_files(self):
        text = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
        for marker in ("validation/", "dist-publish/", "ROADMAP\\.md\\.pre-"):
            self.assertIn(marker, text)

    def test_external_actions_are_pinned_to_full_commit_sha(self):
        pattern = re.compile(r"uses:\s+([^\s@]+)@([^\s#]+)")
        for path in WORKFLOWS.glob("*.yml"):
            text = path.read_text(encoding="utf-8")
            for action, ref in pattern.findall(text):
                if action.startswith("./"):
                    continue
                self.assertRegex(
                    ref, r"^[0-9a-f]{40}$", f"{path.name}: {action}@{ref}"
                )

    def test_docker_base_image_is_pinned_to_digest(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        first_line = dockerfile.splitlines()[0]
        self.assertRegex(
            first_line,
            r"^FROM\s+[^@\s]+@sha256:[0-9a-f]{64}$",
            first_line,
        )

    def test_runtime_dependencies_have_upper_bounds(self):
        for line in (ROOT / "requirements.txt").read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                self.assertIn(",<", line, line)


if __name__ == "__main__":
    unittest.main()
