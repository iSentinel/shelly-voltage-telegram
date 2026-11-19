#!/usr/bin/env python3
"""Resolve a public OCI release tag to its immutable content digest."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import urllib.parse
import urllib.request

VERSION = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+-hw\Z")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
REPOSITORY = "isentinel/shelly-voltage-telegram"
INDEX_TYPES = {
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
}
MAX_BYTES = 2 * 1024 * 1024


class TagBlocked(ValueError):
    """The public tag cannot be bound to one immutable OCI index."""


def arguments() -> argparse.Namespace:
    """Accept one release version tag and optional read-only JSON report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--jsonl", action="store_true")
    return parser.parse_args()


def public_token() -> str:
    """Get a short-lived anonymous GHCR token, never user credentials."""
    params = urllib.parse.urlencode(
        {
            "service": "ghcr.io",
            "scope": f"repository:{REPOSITORY}:pull",
        }
    )
    with urllib.request.urlopen(
        f"https://ghcr.io/token?{params}", timeout=20
    ) as response:
        result = json.load(response)
    token = result.get("token", "")
    if not isinstance(token, str) or not token:
        raise TagBlocked("OCI_ANONYMOUS_TOKEN_UNAVAILABLE")
    return token


def index_digest(tag: str, token: str) -> str:
    """Check advertised tag digest against actual bounded JSON bytes."""
    if not VERSION.fullmatch(tag):
        raise TagBlocked("OCI_RELEASE_TAG_INVALID")
    request = urllib.request.Request(
        f"https://ghcr.io/v2/{REPOSITORY}/manifests/{tag}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": ", ".join(sorted(INDEX_TYPES)),
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        advertised = response.headers.get("Docker-Content-Digest", "")
        content = response.read(MAX_BYTES + 1)
    if not 0 < len(content) <= MAX_BYTES:
        raise TagBlocked("OCI_TAG_INDEX_SIZE_INVALID")
    actual = "sha256:" + hashlib.sha256(content).hexdigest()
    if not DIGEST.fullmatch(advertised) or actual != advertised:
        raise TagBlocked("OCI_TAG_CONTENT_DIGEST_MISMATCH")
    index = json.loads(content)
    if (
        not isinstance(index, dict)
        or index.get("mediaType") not in INDEX_TYPES
    ):
        raise TagBlocked("OCI_TAG_NOT_MULTIARCH_INDEX")
    if not isinstance(index.get("manifests"), list) or not index["manifests"]:
        raise TagBlocked("OCI_TAG_MANIFESTS_MISSING")
    return actual


def blocked_report(jsonl: bool, error: str) -> int:
    """Emit one nonsecret refusal and stable nonzero status."""
    if jsonl:
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "reason": error,
                    "signature_verified": False,
                    "release_authorized": False,
                }
            )
        )
    else:
        print("PUBLIC OCI TAG BLOCKED:", error)
    return 3


def main() -> int:
    """Print only a content-checked digest, not signature approval."""
    args = arguments()
    try:
        if not VERSION.fullmatch(args.tag):
            raise TagBlocked("OCI_RELEASE_TAG_INVALID")
        digest = index_digest(args.tag, public_token())
    except (OSError, ValueError, TypeError, UnicodeError) as exc:
        error = (
            str(exc)
            if isinstance(exc, TagBlocked)
            else "OCI_TAG_READ_UNAVAILABLE"
        )
        return blocked_report(args.jsonl, error)
    if args.jsonl:
        print(
            json.dumps(
                {
                    "status": "PASS_PUBLIC_OCI_TAG_CONTENT_ONLY",
                    "digest": digest,
                    "signature_verified": False,
                    "release_authorized": False,
                    "production_modified": False,
                }
            )
        )
    else:
        print(digest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
