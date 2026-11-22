"""Offline OCI content verification against synthetic descriptors."""

import gzip
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import qa_offline_oci_graph as graph  # noqa: E402

COMMIT = "a" * 40


class GraphTests(unittest.TestCase):
    """Synthetic graph tests do not demonstrate signature verification."""

    def setUp(self):
        """Create an isolated content-addressed fixture."""
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.blobs = self.root / "blobs" / "sha256"
        self.blobs.mkdir(parents=True)

    def blob(self, kind, data):
        """Write one fixture object and return its descriptor."""
        if isinstance(data, dict):
            data = json.dumps(data, sort_keys=True).encode()
        checksum = hashlib.sha256(data).hexdigest()
        (self.blobs / checksum).write_bytes(data)
        return {
            "mediaType": kind,
            "digest": "sha256:" + checksum,
            "size": len(data),
        }

    def image(self, arch):
        """Create an image with one gzip layer and matching DiffID."""
        raw = b"test-layer-" + arch.encode()
        config = self.blob(
            graph.CONFIG,
            {
                "os": "linux",
                "architecture": arch,
                "config": {
                    "Labels": {"org.opencontainers.image.revision": COMMIT}
                },
                "rootfs": {
                    "type": "layers",
                    "diff_ids": ["sha256:" + hashlib.sha256(raw).hexdigest()],
                },
            },
        )
        layer = self.blob(graph.GZIP_LAYER, gzip.compress(raw, mtime=0))
        image = self.blob(
            graph.MANIFEST,
            {
                "mediaType": graph.MANIFEST,
                "config": config,
                "layers": [layer],
            },
        )
        return dict(image, platform={"os": "linux", "architecture": arch})

    def statement_layers(self, image):
        """Create both metadata statements bound to one manifest."""
        layers = []
        for kind, predicate in (
            (
                "https://spdx.dev/Document",
                {"spdxVersion": "SPDX-2.3", "packages": [{}]},
            ),
            (
                "https://slsa.dev/provenance/v1",
                {
                    "buildDefinition": {"buildType": "fixture"},
                    "runDetails": True,
                },
            ),
        ):
            layer = self.blob(
                graph.STATEMENT,
                {
                    "subject": [{"digest": {"sha256": image["digest"][7:]}}],
                    "predicateType": kind,
                    "predicate": predicate,
                },
            )
            layers.append(
                dict(layer, annotations={"in-toto.io/predicate-type": kind})
            )
        return layers

    def attestation(self, image):
        """Create the attestation manifest."""
        layers = self.statement_layers(image)
        config = self.blob("application/vnd.oci.empty.v1+json", {})
        desc = self.blob(
            graph.MANIFEST,
            {
                "mediaType": graph.MANIFEST,
                "config": config,
                "layers": layers,
            },
        )
        return dict(
            desc,
            annotations={
                "vnd.docker.reference.type": "attestation-manifest",
                "vnd.docker.reference.digest": image["digest"],
            },
        )

    def fixture(self):
        """Build a complete two-architecture fixture."""
        images = [self.image(arch) for arch in ("amd64", "arm64")]
        index = self.blob(
            graph.INDEX,
            {
                "mediaType": graph.INDEX,
                "manifests": images + [self.attestation(i) for i in images],
            },
        )
        (self.root / "index.json").write_text(
            json.dumps({"manifests": [index]})
        )
        return index["digest"], images

    def test_complete(self):
        """Matching content passes with all authorization flags false."""
        digest, _ = self.fixture()
        result = graph.verify(self.root, digest, COMMIT)
        self.assertEqual(set(result["images"]), {"amd64", "arm64"})
        self.assertFalse(result["signature_verified"])
        self.assertFalse(result["release_authorized"])

    def test_wrong_index_or_commit(self):
        """Reject another index or mismatched source revision."""
        digest, _ = self.fixture()
        with self.assertRaisesRegex(ValueError, "INDEX_DIGEST_MISMATCH"):
            graph.verify(self.root, "sha256:" + "b" * 64, COMMIT)
        with self.assertRaisesRegex(ValueError, "CONFIG_IDENTITY_MISMATCH"):
            graph.verify(self.root, digest, "b" * 40)

    def test_blob_corruption(self):
        """Reject equal-sized corruption of a referenced blob."""
        digest, images = self.fixture()
        path = self.blobs / images[0]["digest"][7:]
        data = path.read_bytes()
        path.write_bytes(b"X" + data[1:])
        with self.assertRaisesRegex(ValueError, "BLOB_HASH_MISMATCH"):
            graph.verify(self.root, digest, COMMIT)

    def test_diff_id_mismatch(self):
        """Compressed layer parity alone cannot replace DiffID parity."""
        image = self.image("amd64")
        manifest = graph.json_blob(self.root, image)
        config = graph.json_blob(self.root, manifest["config"])
        config["rootfs"]["diff_ids"] = ["sha256:" + "0" * 64]
        manifest["config"] = self.blob(graph.CONFIG, config)
        image = self.blob(graph.MANIFEST, manifest)
        with self.assertRaisesRegex(ValueError, "DIFF_ID_MISMATCH"):
            graph.parse_image(self.root, image, "amd64", COMMIT)

    def test_wrong_statement_subject(self):
        """A metadata statement for another manifest cannot pass."""
        with self.assertRaisesRegex(
            ValueError, "ATTESTATION_SUBJECT_MISMATCH"
        ):
            graph.statement_kind({"subject": []}, "sha256:" + "0" * 64)

    def test_missing_blob(self):
        """Missing graph content is a hard failure."""
        digest, images = self.fixture()
        (self.blobs / images[0]["digest"][7:]).unlink()
        with self.assertRaises(FileNotFoundError):
            graph.verify(self.root, digest, COMMIT)


if __name__ == "__main__":
    unittest.main()
