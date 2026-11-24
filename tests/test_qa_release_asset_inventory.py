"""Read-only release inventory must not authorize unsigned deployments."""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

SOURCE = (
    Path(__file__).resolve().parents[1]
    / "scripts/qa-release-asset-inventory.py"
)
SPEC = importlib.util.spec_from_file_location("svt_release_inventory", SOURCE)
assert SPEC is not None and SPEC.loader is not None
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)

VERSION = "0.5.26-hw"
COMMIT = "a" * 40
DIGEST = "sha256:" + "b" * 64
BASE = "https://github.com/iSentinel/shelly-voltage-telegram/"


class ReleaseAssetInventoryTests(unittest.TestCase):
    """Use synthetic public JSON and never invoke a remote API or Docker."""

    def setUp(self) -> None:
        """Build a well-formed full release manifest in a private fixture."""
        folder = tempfile.TemporaryDirectory(prefix="svt-public-release-")
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.path = self.root / "release.json"
        self.tagpath = self.root / "tag-ref.json"
        self.tagref = {
            "ref": "refs/tags/v" + VERSION,
            "object": {"type": "commit", "sha": COMMIT},
        }
        self.release = {
            "tag_name": "v" + VERSION,
            "draft": False,
            "prerelease": False,
            "html_url": BASE + "releases/tag/v" + VERSION,
            "assets": [
                self.asset(name)
                for name in sorted(CHECK.expected_assets(VERSION))
            ],
        }
        self.save()

    def asset(self, name: str) -> dict:
        """Return one public GitHub release asset with exact metadata."""
        return {
            "name": name,
            "browser_download_url": BASE
            + "releases/download/v"
            + VERSION
            + "/"
            + name,
            "state": "uploaded",
            "size": 100,
        }

    def args(self) -> argparse.Namespace:
        """Supply immutable-looking inputs without claiming provenance."""
        return argparse.Namespace(
            release_json=self.path,
            tag_ref_json=self.tagpath,
            version=VERSION,
            commit=COMMIT,
            digest=DIGEST,
            jsonl=True,
        )

    def save(self) -> None:
        """Update only the scratch JSON source file."""
        self.path.write_text(json.dumps(self.release), encoding="utf-8")
        self.tagpath.write_text(json.dumps(self.tagref), encoding="utf-8")

    def assert_blocked(self, code: str) -> None:
        """Require a stable refusal code on untrusted public metadata."""
        with self.assertRaisesRegex(CHECK.InventoryBlocked, code):
            CHECK.check(self.args())

    def test_complete_inventory_is_not_a_signature_proof(self) -> None:
        """A complete asset listing never grants an installation permit."""
        report = CHECK.check(self.args())
        self.assertEqual(report["status"], "PASS_PUBLIC_INVENTORY_ONLY")
        self.assertTrue(report["tag_commit_matches_input"])
        self.assertFalse(report["tag_metadata_cryptographically_verified"])
        self.assertFalse(report["source_signature_verified"])
        self.assertFalse(report["oci_signature_verified"])
        self.assertFalse(report["release_authorized"])
        self.assertFalse(report["rollback_authorized"])
        self.assertFalse(report["production_modified"])

    def test_missing_signing_bundle_is_blocking(self) -> None:
        """Published SHA256SUMS alone do not authenticate source bytes."""
        self.release["assets"] = [
            x
            for x in self.release["assets"]
            if not x["name"].endswith(".sigstore.json")
        ]
        self.save()
        self.assert_blocked("INVENTORY_REQUIRED_ASSETS_MISSING")

    def test_missing_archive_or_checksums_is_blocking(self) -> None:
        """Every required public release asset is checked by exact name."""
        for name in (
            "SHA256SUMS",
            f"shelly-voltage-telegram-{VERSION}.tar.gz",
        ):
            with self.subTest(name=name):
                original = list(self.release["assets"])
                self.release["assets"] = [
                    item for item in original if item["name"] != name
                ]
                self.save()
                self.assert_blocked("INVENTORY_REQUIRED_ASSETS_MISSING")
                self.release["assets"] = original

    def test_wrong_tag_draft_or_prerelease_is_blocking(self) -> None:
        """Unsigned, unpublished or differently tagged records are refused."""
        for key, value, reason in (
            ("tag_name", "v0.5.25-hw", "RELEASE_TAG_MISMATCH"),
            ("draft", True, "RELEASE_UNPUBLISHED"),
            ("prerelease", True, "RELEASE_UNPUBLISHED"),
            ("html_url", "https://evil.test/", "RELEASE_PAGE_MISMATCH"),
        ):
            with self.subTest(key=key):
                original = self.release[key]
                self.release[key] = value
                self.save()
                self.assert_blocked(reason)
                self.release[key] = original

    def test_malformed_commit_and_digest_are_refused(self) -> None:
        """Mutable refs and non-immutable OCI tags cannot pass."""
        args = self.args()
        args.commit = "refs/heads/main"
        with self.assertRaisesRegex(CHECK.InventoryBlocked, "COMMIT_INVALID"):
            CHECK.check(args)
        args = self.args()
        args.digest = "latest"
        with self.assertRaisesRegex(
            CHECK.InventoryBlocked, "OCI_DIGEST_INVALID"
        ):
            CHECK.check(args)

    def test_wrong_tag_commit_is_blocking(self) -> None:
        """The public exact tag ref must resolve to requested full SHA."""
        self.tagref["object"]["sha"] = "b" * 40
        self.save()
        self.assert_blocked("INVENTORY_TAG_COMMIT_MISMATCH")

    def test_annotated_tag_without_peeled_commit_is_blocking(self) -> None:
        """An unresolved tag object is not an immutable source commit."""
        self.tagref["object"]["type"] = "tag"
        self.save()
        self.assert_blocked("INVENTORY_TAG_PEELED_COMMIT_REQUIRED")

    def test_tag_reference_must_match_release_version(self) -> None:
        """The tag and release may not point to different versions."""
        self.tagref["ref"] = "refs/tags/v0.5.25-hw"
        self.save()
        self.assert_blocked("INVENTORY_TAG_REF_MISMATCH")

    def test_forged_asset_hostname_or_wrong_path_is_refused(self) -> None:
        """No public asset may be served from another host or release."""
        for url in (
            "https://github.com.evil.test/archive",
            BASE + "releases/download/v0.5.25-hw/SHA256SUMS",
            "http://github.com/something",
        ):
            with self.subTest(url=url):
                self.release["assets"][0]["browser_download_url"] = url
                self.save()
                self.assert_blocked("ASSET_URL_INVALID")

    def test_duplicate_asset_and_invalid_upload_metadata_is_refused(
        self,
    ) -> None:
        """Do not assume ambiguous or partially uploaded files exist."""
        original = list(self.release["assets"])
        self.release["assets"].append(dict(original[0]))
        self.save()
        self.assert_blocked("ASSET_DUPLICATED")
        self.release["assets"] = original
        for key, value in (("state", "new"), ("size", 0)):
            self.release["assets"][0][key] = value
            self.save()
            self.assert_blocked("ASSET_NOT_UPLOADED")
            self.release["assets"][0] = original[0]

    def test_linked_or_invalid_json_input_is_refused(self) -> None:
        """Do not follow a symlink or silently accept malformed JSON."""
        alias = self.root / "alias.json"
        alias.symlink_to(self.path)
        args = self.args()
        args.release_json = alias
        with self.assertRaisesRegex(CHECK.InventoryBlocked, "FILE_UNSAFE"):
            CHECK.check(args)
        self.path.write_text("{invalid-json", encoding="utf-8")
        result = subprocess.run(
            [
                "python3",
                str(SOURCE),
                "--release-json",
                str(self.path),
                "--tag-ref-json",
                str(self.tagpath),
                "--version",
                VERSION,
                "--commit",
                COMMIT,
                "--digest",
                DIGEST,
                "--jsonl",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        self.assertEqual(result.returncode, 3)
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "BLOCKED")
        self.assertFalse(report["release_authorized"])


if __name__ == "__main__":
    unittest.main()
