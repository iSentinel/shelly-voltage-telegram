"""Offline tests for read-only public image verification."""

from __future__ import annotations

import runpy
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = runpy.run_path(str(ROOT / "scripts/verify-published-image.py"))
DIGEST = "sha256:" + "a" * 64
ARM_DIGEST = "sha256:" + "b" * 64
ATTEST_DIGEST = "sha256:" + "c" * 64


class PublishedImageTests(unittest.TestCase):
    """Verify attestation lookup, platform checks, and digest validation."""

    def test_valid_digest(self) -> None:
        """Accept only exact SHA-256 OCI references."""

        self.assertEqual(SCRIPT["require_digest"](DIGEST), DIGEST)
        with self.assertRaises(ValueError):
            SCRIPT["require_digest"]("latest")

    def test_find_both_platforms(self) -> None:
        """Find Linux amd64 and arm64 in an index with attestations."""

        index = self.sample_index()
        self.assertEqual(
            set(SCRIPT["platform_manifests"](index)), {"amd64", "arm64"}
        )

    def test_missing_platform_fails(self) -> None:
        """Reject an index without the other required architecture."""

        index = self.sample_index()
        index["manifests"].pop(1)
        with self.assertRaises(ValueError):
            SCRIPT["platform_manifests"](index)

    def test_attestation_is_bound_to_image(self) -> None:
        """Do not accept attestations for a different image digest."""

        index = self.sample_index()
        match = SCRIPT["attestation_descriptor"](index, DIGEST)
        self.assertEqual(match["digest"], ATTEST_DIGEST)
        with self.assertRaises(ValueError):
            SCRIPT["attestation_descriptor"](index, ARM_DIGEST)

    def test_source_revision_label(self) -> None:
        """Return the recorded Git revision from an image config."""

        data = {
            "config": {"Labels": {"org.opencontainers.image.revision": "x"}}
        }
        self.assertEqual(SCRIPT["image_revision"](data), "x")

    def test_predicate_subject_validation(self) -> None:
        """Reject a statement that does not describe the expected image."""

        source = {
            "subject": [{"digest": {"sha256": "d" * 64}}],
            "predicateType": "https://spdx.dev/Document",
            "predicate": {"spdxVersion": "SPDX-2.3", "packages": [{}]},
        }
        manifest = {
            "layers": [
                {"mediaType": "application/vnd.in-toto+json", "digest": DIGEST}
            ]
        }
        fn = SCRIPT["statement_types"]
        with patch.dict(fn.__globals__, {"fetch_object": lambda *_: source}):
            with self.assertRaises(ValueError):
                fn("token", manifest, DIGEST)

    @staticmethod
    def sample_index() -> dict:
        """Return two architectures and one linked attestation."""

        return {
            "manifests": [
                {
                    "digest": DIGEST,
                    "platform": {"os": "linux", "architecture": "amd64"},
                },
                {
                    "digest": ARM_DIGEST,
                    "platform": {"os": "linux", "architecture": "arm64"},
                },
                {
                    "digest": ATTEST_DIGEST,
                    "annotations": {
                        "vnd.docker.reference.type": "attestation-manifest",
                        "vnd.docker.reference.digest": DIGEST,
                    },
                },
            ]
        }


if __name__ == "__main__":
    unittest.main()
