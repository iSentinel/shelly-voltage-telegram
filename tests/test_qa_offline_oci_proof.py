"""Test exact OCI byte-chain review without registry or Docker access."""

import gzip
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "proof",
    Path(__file__).resolve().parents[1] / "scripts/qa-offline-oci-proof.py",
)
assert SPEC and SPEC.loader
OCI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(OCI)


class OfflineOciTests(unittest.TestCase):
    """Construct a synthetic index, manifest, config and compressed layer."""

    def setUp(self):
        """Use protected-path mock only; actual bytes are hashed."""
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.blobs = self.root / "blobs"
        self.blobs.mkdir()
        self.commit = "b" * 40
        guard = patch.object(OCI, "checked_file", side_effect=self.file_size)
        guard.start()
        self.addCleanup(guard.stop)
        self.make_fixture()

    def file_size(self, path, maximum):
        """Permit temp test paths but keep the size bound."""
        size = path.stat().st_size
        if not 0 < size <= maximum:
            raise ValueError("invalid fixture size")
        return size

    def desc(self, raw, media):
        """Create a descriptor from exact bytes."""
        return {
            "digest": "sha256:" + OCI.PAIR.digest(raw),
            "size": len(raw),
            "mediaType": media,
        }

    def blob(self, raw, media):
        """Write exact bytes using their digest as filename."""
        desc = self.desc(raw, media)
        path = self.blobs / desc["digest"].removeprefix("sha256:")
        path.write_bytes(raw)
        return desc, path

    def make_fixture(self):
        """Create one valid amd64 image with one gzip layer."""
        data = b"synthetic layer bytes"
        self.diffid = "sha256:" + OCI.PAIR.digest(data)
        layer, self.layer_path = self.blob(gzip.compress(data), OCI.LAYER)
        config = {
            "os": "linux",
            "architecture": "amd64",
            "config": {
                "Labels": {"org.opencontainers.image.revision": self.commit}
            },
            "rootfs": {"type": "layers", "diff_ids": [self.diffid]},
        }
        self.config, self.config_path = self.blob(
            json.dumps(config).encode(), OCI.CONFIG
        )
        self.manifest = {
            "schemaVersion": 2,
            "mediaType": OCI.MANIFEST,
            "config": self.config,
            "layers": [layer],
        }
        self.write_manifest()

    def write_manifest(self):
        """Publish synthetic manifest and index; return test trust anchor."""
        raw = json.dumps(self.manifest).encode()
        (self.blobs / "manifest.json").write_bytes(raw)
        child = self.desc(raw, OCI.MANIFEST)
        child["platform"] = {"os": "linux", "architecture": "amd64"}
        self.index = {
            "schemaVersion": 2,
            "mediaType": OCI.INDEX,
            "manifests": [child],
        }
        self.write_index()

    def write_index(self):
        """Pin the raw synthetic index bytes."""
        raw = json.dumps(self.index).encode()
        (self.root / "index.json").write_bytes(raw)
        self.anchor = "sha256:" + OCI.PAIR.digest(raw)

    def review(self):
        """Review the synthetic byte chain."""
        return OCI.verify_chain(self.root, self.anchor, self.commit)

    def test_valid_chain(self):
        """All descriptors and uncompressed layer identity must match."""
        proof = self.review()
        self.assertEqual(proof["image_id"], self.config["digest"])
        self.assertEqual(proof["diff_ids"], [self.diffid])

    def test_changed_index(self):
        """A whitespace change invalidates the original digest."""
        path = self.root / "index.json"
        path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "digest or size"):
            self.review()

    def test_ambiguous_platform(self):
        """Refuse two matching platform descriptors."""
        self.index["manifests"] *= 2
        self.write_index()
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            self.review()

    def test_changed_layer(self):
        """Detect same-size compressed blob corruption."""
        raw = self.layer_path.read_bytes()
        self.layer_path.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
        with self.assertRaisesRegex(ValueError, "layer digest"):
            self.review()

    def test_wrong_diffid(self):
        """Reject a valid compressed blob with a wrong config DiffID."""
        config = json.loads(self.config_path.read_bytes())
        config["rootfs"]["diff_ids"] = ["sha256:" + "f" * 64]
        self.manifest["config"], _ = self.blob(
            json.dumps(config).encode(), OCI.CONFIG
        )
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "uncompressed"):
            self.review()

    def test_local_identity(self):
        """Match actual ordered RootFS layers, not only image ID."""
        proof = self.review()
        image = {
            "Id": proof["image_id"],
            "Os": "linux",
            "Architecture": "amd64",
            "Config": {
                "Labels": {"org.opencontainers.image.revision": self.commit}
            },
            "RootFS": {"Layers": proof["diff_ids"]},
        }
        OCI.verify_local(proof, image)
        image["RootFS"]["Layers"] = []
        with self.assertRaisesRegex(ValueError, "local image identity"):
            OCI.verify_local(proof, image)
