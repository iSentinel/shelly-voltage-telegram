"""Offline pair checks must not confuse content parity with authorization."""

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import qa_verify_offline_pair as pair  # noqa: E402


class LocalImageTests(unittest.TestCase):
    """Check exact local content identity independent of registry tags."""

    def setUp(self):
        """Use a synthetic Docker inspect record."""
        self.commit = "a" * 40
        self.image = {
            "config": "sha256:" + "b" * 64,
            "diff_ids": ["sha256:" + "c" * 64, "sha256:" + "d" * 64],
        }
        self.info = {
            "Id": self.image["config"],
            "Architecture": "amd64",
            "Os": "linux",
            "RootFS": {"Type": "layers", "Layers": self.image["diff_ids"]},
            "Config": {
                "Labels": {"org.opencontainers.image.revision": self.commit}
            },
            "RepoDigests": [],
        }

    def check(self, info):
        """Invoke the independent local identity check."""
        pair.inspect_local(info, self.image, "amd64", self.commit)

    def test_unregistered_image_content(self):
        """A missing digest alias does not invalidate content evidence."""
        self.check(self.info)

    def test_mismatch(self):
        """Reject different config, architecture, order and revision."""
        changes = [
            ("Id", "sha256:" + "f" * 64),
            ("Architecture", "arm64"),
            ("Os", "windows"),
            (
                "RootFS",
                {"Type": "layers", "Layers": self.image["diff_ids"][::-1]},
            ),
            ("Config", {"Labels": {"org.opencontainers.image.revision": "x"}}),
        ]
        for key, value in changes:
            with self.subTest(key=key):
                info = copy.deepcopy(self.info)
                info[key] = value
                with self.assertRaisesRegex(
                    ValueError, "LOCAL_IMAGE_MISMATCH"
                ):
                    self.check(info)


if __name__ == "__main__":
    unittest.main()
