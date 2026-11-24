"""Offline GitHub draft release must prove asset parity before publication."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
WORKFLOW = (SOURCE / ".github/workflows/release.yml").read_text()


def run_step(name: str) -> str:
    """Extract one actual GitHub Action Bash run block for execution."""
    text = WORKFLOW.split("      - name: " + name + "\n", 1)[1]
    block = text.split("      - name:", 1)[0]
    raw = block.split("        run: |\n", 1)[1]
    return "\n".join(line[10:] for line in raw.splitlines()) + "\n"


CREATE = run_step("Create GitHub Release")
UPLOAD = run_step("Upload and verify draft source assets")
PUBLISH = run_step("Publish verified GitHub Release")
MOCKS = """gh() {
  echo "$2" >> "$MOCK_CALLS"
  case "$2" in
    view)
      if [ "$#" -eq 3 ]; then
        [ "$MOCK_EXISTS" = 1 ]; return $?
      fi
      case "$*" in
        *tagName,isDraft,assets*) printf '%s\\n' "$MOCK_DRAFT_JSON" ;;
        *"isDraft --jq"*) printf 'false\\n' ;;
        *) return 93 ;;
      esac ;;
    create) return 0 ;;
    upload) return "$MOCK_UPLOAD_EXIT" ;;
    download)
      cp "$MOCK_SOURCE_DIR"/* "$5/"
      if [ -n "$MOCK_CORRUPT" ]; then
        printf x >> "$5/$MOCK_CORRUPT"
      fi ;;
    edit) return 0 ;;
    *) return 94 ;;
  esac
}
"""


class SourceReleaseDraftPublicationTests(unittest.TestCase):
    """The draft upload step is a hard barrier to public release edit."""

    def setUp(self) -> None:
        """Build three synthetic, non-secret release assets in scratch."""
        temp = tempfile.TemporaryDirectory(prefix="svt-qa-draft-release-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.version = "0.5.26-hw"
        self.root.joinpath("VERSION").write_text(self.version + "\n")
        self.dist = self.root / "dist"
        self.dist.mkdir()
        self.files = (
            self.dist / f"shelly-voltage-telegram-{self.version}.tar.gz",
            self.dist
            / f"shelly-voltage-telegram-{self.version}.tar.gz.sigstore.json",
            self.dist / "SHA256SUMS",
        )
        for index, path in enumerate(self.files):
            path.write_bytes(f"test-only-{index}".encode())
        self.log = self.root / "commands"

    def manifest(self, omit: str = "", draft: bool = True) -> str:
        """Represent exact gh release view JSON asset name/size fields."""
        return json.dumps(
            {
                "tagName": "v" + self.version,
                "isDraft": draft,
                "assets": [
                    {"name": p.name, "size": p.stat().st_size}
                    for p in self.files
                    if p.name != omit
                ],
            }
        )

    def run_flow(
        self,
        omit: str = "",
        draft: bool = True,
        corrupt: str = "",
        exists: bool = False,
        upload_exit: int = 0,
    ) -> subprocess.CompletedProcess:
        """Invoke the real three GitHub shell blocks with a fake gh CLI."""
        env = dict(
            os.environ,
            RELEASE_TAG="v" + self.version,
            MOCK_CALLS=str(self.log),
            MOCK_EXISTS="1" if exists else "0",
            MOCK_SOURCE_DIR=str(self.dist),
            MOCK_DRAFT_JSON=self.manifest(omit, draft),
            MOCK_CORRUPT=corrupt,
            MOCK_UPLOAD_EXIT=str(upload_exit),
        )
        return subprocess.run(
            ["bash"],
            input=MOCKS + CREATE + UPLOAD + PUBLISH,
            cwd=self.root,
            env=env,
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )

    def test_shell_blocks_parse(self) -> None:
        """None of the actual workflow stages has invalid Bash syntax."""
        for script in (CREATE, UPLOAD, PUBLISH):
            result = subprocess.run(
                ["bash", "-n"],
                input=script,
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_complete_draft_can_be_published(self) -> None:
        """All three source assets must roundtrip byte-for-byte."""
        result = self.run_flow()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DRAFT_SOURCE_UPLOAD_BYTE_PARITY: PASS", result.stdout)
        self.assertIn("VERIFIED_GITHUB_RELEASE_PUBLISHED: PASS", result.stdout)
        self.assertIn("edit", self.log.read_text().splitlines())

    def test_missing_sigstore_bundle_never_publishes(self) -> None:
        """A draft missing the bundle must not become a public release."""
        result = self.run_flow(omit=self.files[1].name)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DRAFT_ASSET_COUNT_INVALID", result.stderr)
        self.assertNotIn("edit", self.log.read_text().splitlines())

    def test_corrupted_download_never_publishes(self) -> None:
        """Assets returned by GitHub must equal source-signed local bytes."""
        result = self.run_flow(corrupt=self.files[1].name)
        self.assertEqual(result.returncode, 11, result.stderr)
        self.assertIn("draft source asset byte mismatch", result.stderr)
        self.assertNotIn("edit", self.log.read_text().splitlines())

    def test_nondraft_or_old_release_never_overwrites(self) -> None:
        """Do not modify an existing or publicly visible release."""
        for kwargs in ({"draft": False}, {"exists": True}):
            with self.subTest(kwargs=kwargs):
                self.log.unlink(missing_ok=True)
                result = self.run_flow(**kwargs)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("edit", self.log.read_text().splitlines())

    def test_upload_failure_keeps_release_unpublished(self) -> None:
        """Partial upload failures must not pass the draft boundary."""
        result = self.run_flow(upload_exit=17)
        self.assertEqual(result.returncode, 17, result.stderr)
        self.assertNotIn("edit", self.log.read_text().splitlines())


if __name__ == "__main__":
    unittest.main()
