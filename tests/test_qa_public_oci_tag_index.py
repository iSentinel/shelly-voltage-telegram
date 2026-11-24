"""Offline public GHCR tag resolution must bind bytes, not mutable tags."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch

PATH = (
    Path(__file__).resolve().parents[1] / "scripts/qa-public-oci-tag-index.py"
)
SPEC = importlib.util.spec_from_file_location("qa_public_oci_tag_index", PATH)
assert SPEC is not None and SPEC.loader is not None
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)
TAG = "v0.5.26-hw"
INDEX = {
    "mediaType": "application/vnd.oci.image.index.v1+json",
    "manifests": [
        {
            "digest": "sha256:" + "a" * 64,
            "platform": {
                "os": "linux",
                "architecture": "amd64",
            },
        },
    ],
}


class FakeResponse:
    """Minimal HTTP context for two immutable in-memory registry fixtures."""

    def __init__(self, content: bytes, digest: str) -> None:
        """Return bounded bytes and a registry-advertised content digest."""
        self.data = io.BytesIO(content)
        self.headers = {"Docker-Content-Digest": digest}

    def __enter__(self):
        """Return the static fake response."""
        return self

    def __exit__(self, *_args):
        """Release the backing in-memory stream."""
        self.data.close()

    def read(self, size=-1):
        """Use the same read limit behavior as urllib responses."""
        return self.data.read(size)


class PublicOciTagIndexTests(unittest.TestCase):
    """Tag retrieval never proves a signature or authorizes production."""

    def setUp(self) -> None:
        """Build one small immutable multiarch-index fixture."""
        self.content = json.dumps(INDEX).encode()
        self.digest = "sha256:" + hashlib.sha256(self.content).hexdigest()

    def resolve(self, digest: str | None = None, content: bytes | None = None):
        """Invoke tag resolver with no real token or registry traffic."""
        body = self.content if content is None else content
        advertised = self.digest if digest is None else digest
        response = FakeResponse(body, advertised)
        with patch.object(
            CHECK.urllib.request, "urlopen", return_value=response
        ) as fetch:
            result = CHECK.index_digest(TAG, "fake-qa-token")
        request = fetch.call_args.args[0]
        self.assertEqual(
            request.full_url,
            "https://ghcr.io/v2/isentinel/shelly-voltage-telegram/"
            "manifests/v0.5.26-hw",
        )
        return result

    def test_public_index_returns_only_content_digest(self) -> None:
        """One exact hash is returned without any cryptographic claim."""
        self.assertEqual(self.resolve(), self.digest)

    def test_wrong_advertised_hash_is_blocked(self) -> None:
        """No GHCR tag can substitute a digest unrelated to index bytes."""
        with self.assertRaisesRegex(CHECK.TagBlocked, "DIGEST_MISMATCH"):
            self.resolve(digest="sha256:" + "b" * 64)

    def test_mutable_or_malformed_tags_are_refused(self) -> None:
        """Do not construct a remote URL from arbitrary tag text."""
        for value in ("latest", "sha-abcdef1", "../foo", "v0.5.26"):
            with (
                self.subTest(tag=value),
                self.assertRaisesRegex(CHECK.TagBlocked, "TAG_INVALID"),
            ):
                CHECK.index_digest(value, "qa-token")

    def test_missing_manifest_rejected(self) -> None:
        """An empty or non-index descriptor is never an accepted release."""
        for index in (
            {"mediaType": INDEX["mediaType"], "manifests": []},
            {
                "mediaType": "application/vnd.oci.image.manifest.v1+json",
                "manifests": INDEX["manifests"],
            },
        ):
            body = json.dumps(index).encode()
            digest = "sha256:" + hashlib.sha256(body).hexdigest()
            with (
                self.subTest(index=index),
                self.assertRaises(CHECK.TagBlocked),
            ):
                self.resolve(digest=digest, content=body)

    def test_oversized_index_is_blocked(self) -> None:
        """Reject oversized JSON before parsing or accepting any digest."""
        data = self.content + b" " * CHECK.MAX_BYTES
        with self.assertRaisesRegex(CHECK.TagBlocked, "SIZE_INVALID"):
            self.resolve(content=data)

    def test_anonymous_token_is_requested_for_public_package(self) -> None:
        """Do not read local Docker or production credentials."""
        response = FakeResponse(b'{"token":"qa-only-public-token"}', "")
        with patch.object(
            CHECK.urllib.request, "urlopen", return_value=response
        ) as fetch:
            token = CHECK.public_token()
        self.assertEqual(token, "qa-only-public-token")
        self.assertIn("scope=repository%3A", fetch.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
