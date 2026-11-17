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


COMMIT = "39bd933a61cbe2ace06911014e87e49df8475c39"
SPDX = "https://spdx.dev/Document"
SLSA = "https://slsa.dev/provenance/v1"


def add_fixture_platform(index, objects, arch, letters):
    """Attach one image, its revision and two subject-bound attestations."""

    img, cfg, att, sbom, provenance = (
        "sha256:" + letter * 64 for letter in letters
    )
    index["manifests"].append(
        {"digest": img, "platform": {"os": "linux", "architecture": arch}}
    )
    index["manifests"].append(
        {
            "digest": att,
            "annotations": {
                "vnd.docker.reference.type": "attestation-manifest",
                "vnd.docker.reference.digest": img,
            },
        }
    )
    objects[img] = {"config": {"digest": cfg}}
    objects[cfg] = {
        "config": {"Labels": {"org.opencontainers.image.revision": COMMIT}}
    }
    objects[att] = {
        "layers": [
            {"mediaType": "application/vnd.in-toto+json", "digest": layer}
            for layer in (sbom, provenance)
        ]
    }
    subject = [{"digest": {"sha256": img.split(":")[1]}}]
    objects[sbom] = {
        "subject": subject,
        "predicateType": SPDX,
        "predicate": {"spdxVersion": "SPDX-2.3", "packages": [{}]},
    }
    objects[provenance] = {
        "subject": [{"digest": {"sha256": img.split(":")[1]}}],
        "predicateType": SLSA,
        "predicate": {"buildDefinition": {"buildType": "synthetic"}},
    }
    return img, cfg, att, sbom, provenance


def synthetic_registry():
    """Create a complete dual-architecture registry fixture."""

    index = {"mediaType": SCRIPT["INDEX_TYPES"][0], "manifests": []}
    objects = {}
    amd = add_fixture_platform(index, objects, "amd64", "ace13")
    arm = add_fixture_platform(index, objects, "arm64", "bdf24")
    return index, objects, amd, arm


def audit_without_network(index, objects, commit=COMMIT):
    """Run the real OCI verifier with only synthetic registry data."""

    fn = SCRIPT["verify_image"]

    def fake(_token, digest, _kind):
        return objects[digest]

    with patch.dict(fn.__globals__, {"fetch_object": fake}):
        fn("synthetic-token", index, commit)


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
        with (
            patch.dict(fn.__globals__, {"fetch_object": lambda *_: source}),
            self.assertRaises(ValueError),
        ):
            fn("token", manifest, DIGEST)

    def test_accept_dual_architecture_attestation_fixture(self) -> None:
        """Both Linux platforms need matching revision, SPDX and SLSA."""

        index, objects, _, _ = synthetic_registry()
        audit_without_network(index, objects)

    def test_reject_signed_old_image_under_new_source_commit(self) -> None:
        """Source metadata prevents an old release from impersonating QA."""

        index, objects, _, _ = synthetic_registry()
        with self.assertRaisesRegex(ValueError, "Unexpected source revision"):
            audit_without_network(
                index, objects, "4bbc9462134c312090ef4a2e73edf68e5d44d5e5"
            )

    def test_reject_arm64_without_its_attestation(self) -> None:
        """An amd64 attestation cannot substitute for arm64 evidence."""

        index, objects, _, arm = synthetic_registry()
        index["manifests"] = [
            entry for entry in index["manifests"] if entry["digest"] != arm[2]
        ]
        with self.assertRaisesRegex(ValueError, "Exactly one attestation"):
            audit_without_network(index, objects)

    def test_reject_duplicate_attestation_for_one_image(self) -> None:
        """Ambiguous attestation manifests must be rejected."""

        index, objects, amd, _ = synthetic_registry()
        item = next(
            item for item in index["manifests"] if item["digest"] == amd[2]
        )
        index["manifests"].append(dict(item))
        with self.assertRaisesRegex(ValueError, "Exactly one attestation"):
            audit_without_network(index, objects)

    def test_reject_incorrect_spdx_version(self) -> None:
        """A wrong SPDX version is not valid release SBOM evidence."""

        index, objects, _, arm = synthetic_registry()
        objects[arm[3]]["predicate"]["spdxVersion"] = "SPDX-2.2"
        with self.assertRaisesRegex(ValueError, "SPDX 2.3"):
            audit_without_network(index, objects)

    def test_reject_empty_spdx_package_list(self) -> None:
        """No real packages means there is no accepted SPDX inventory."""

        index, objects, amd, _ = synthetic_registry()
        objects[amd[3]]["predicate"]["packages"] = []
        with self.assertRaisesRegex(ValueError, "SPDX has no packages"):
            audit_without_network(index, objects)

    def test_reject_missing_slsa_provenance(self) -> None:
        """A separate SLSA statement is mandatory per architecture."""

        index, objects, _, arm = synthetic_registry()
        objects[arm[2]]["layers"].pop()
        with self.assertRaisesRegex(ValueError, "SLSA provenance missing"):
            audit_without_network(index, objects)

    def test_reject_provenance_without_build_information(self) -> None:
        """SLSA without build definition is not valid release evidence."""

        index, objects, amd, _ = synthetic_registry()
        objects[amd[4]]["predicate"] = {}
        with self.assertRaisesRegex(ValueError, "SLSA build information"):
            audit_without_network(index, objects)

    def test_reject_sbom_subject_for_another_image(self) -> None:
        """The SBOM's image subject must match the indexed manifest."""

        index, objects, amd, _ = synthetic_registry()
        objects[amd[3]]["subject"][0]["digest"]["sha256"] = "f" * 64
        with self.assertRaisesRegex(ValueError, "subject is not"):
            audit_without_network(index, objects)

    def test_reject_provenance_subject_for_another_image(self) -> None:
        """The provenance subject cannot refer to a different OCI image."""

        index, objects, _, arm = synthetic_registry()
        objects[arm[4]]["subject"][0]["digest"]["sha256"] = "f" * 64
        with self.assertRaisesRegex(ValueError, "subject is not"):
            audit_without_network(index, objects)

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
