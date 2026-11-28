"""Test offline OCI layout verification without a container daemon."""

import gzip
import hashlib
import runpy
import tempfile
import unittest
from pathlib import Path

SCRIPT = runpy.run_path(
    str(
        Path(__file__).resolve().parents[1]
        / "scripts/qa-stage-offline-base-layout.py"
    )
)


class OfflineBaseTests(unittest.TestCase):
    """Require content and platform identity before staging."""

    def test_layer_diff_id(self):
        """Correct compressed bytes expand to the expected DiffID."""
        raw = b"test layer"
        digest = "sha256:" + hashlib.sha256(raw).hexdigest()
        SCRIPT["check_layer"](gzip.compress(raw), digest)

    def test_wrong_diff_id(self):
        """Reject a valid gzip stream with different expanded content."""
        with self.assertRaisesRegex(ValueError, "DiffID"):
            SCRIPT["check_layer"](
                gzip.compress(b"wrong"), "sha256:" + "0" * 64
            )

    def test_platform_binding(self):
        """Require exactly one Linux descriptor matching the target."""
        expected = "sha256:" + SCRIPT["PREPARE"]["BASE_MANIFESTS"]["amd64"]
        row = {
            "digest": expected,
            "platform": {"os": "linux", "architecture": "amd64"},
        }
        self.assertEqual(
            SCRIPT["platform_descriptor"]({"manifests": [row]}, "amd64"), row
        )
        with self.assertRaisesRegex(ValueError, "descriptor mismatch"):
            SCRIPT["platform_descriptor"]({"manifests": [row, row]}, "amd64")
        with self.assertRaisesRegex(ValueError, "descriptor mismatch"):
            SCRIPT["platform_descriptor"]({"manifests": [row]}, "arm64")

    def test_blob_identity(self):
        """Only exact descriptor size and hash can be staged."""
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "blob"
            raw = b"original"
            source.write_bytes(raw)
            digest = hashlib.sha256(raw).hexdigest()
            descriptor = {"digest": "sha256:" + digest, "size": len(raw)}
            files = {}
            SCRIPT["add_blob"](files, source, descriptor)
            self.assertEqual(files, {"blobs/sha256/" + digest: raw})
            source.write_bytes(b"modified")
            with self.assertRaisesRegex(ValueError, "SHA256"):
                SCRIPT["add_blob"]({}, source, descriptor)
