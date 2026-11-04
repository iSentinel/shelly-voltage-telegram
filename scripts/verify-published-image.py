#!/usr/bin/env python3
"""Verify signed release image contents through the public OCI registry."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import urllib.parse
import urllib.request
from typing import Any

REPOSITORY = "isentinel/shelly-voltage-telegram"
ARCHITECTURES = ("amd64", "arm64")
INDEX_TYPES = (
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
)


def arguments() -> argparse.Namespace:
    """Read the immutable image digest and expected source revision."""

    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument(
        "--digest", required=True, help="Pinned image SHA-256 digest"
    )
    cli.add_argument("--commit", required=True, help="Expected Git commit SHA")
    return cli.parse_args()


def require_digest(value: str) -> str:
    """Validate an OCI SHA-256 reference before using it in a URL."""

    if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise ValueError(f"Invalid OCI digest: {value!r}")
    return value


def token_for_public_image() -> str:
    """Retrieve a short-lived anonymous token for the public GHCR package."""

    query = urllib.parse.urlencode(
        {"service": "ghcr.io", "scope": f"repository:{REPOSITORY}:pull"}
    )
    with urllib.request.urlopen(
        f"https://ghcr.io/token?{query}", timeout=20
    ) as response:
        token = json.load(response).get("token", "")
    if not token:
        raise RuntimeError("Public GHCR token is missing")
    return str(token)


def fetch_object(token: str, digest: str, kind: str) -> dict[str, Any]:
    """Download a registry JSON object and verify its digest."""

    require_digest(digest)
    if kind not in {"manifests", "blobs"}:
        raise ValueError("Unsupported OCI resource kind")
    url = f"https://ghcr.io/v2/{REPOSITORY}/{kind}/{digest}"
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": (
                ", ".join(INDEX_TYPES)
                + ", application/vnd.oci.image.manifest.v1+json"
            ),
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        content = response.read()
    if hashlib.sha256(content).hexdigest() != digest.partition(":")[2]:
        raise ValueError(f"OCI {kind} SHA-256 does not match {digest}")
    return json.loads(content)


def platform_manifests(index: dict[str, Any]) -> dict[str, dict]:
    """Return the pinned Linux image manifests for both architectures."""

    output: dict[str, dict] = {}
    for item in index.get("manifests", []):
        platform = item.get("platform", {})
        arch = platform.get("architecture")
        if platform.get("os") == "linux" and arch in ARCHITECTURES:
            if arch in output:
                raise ValueError(f"Duplicate {arch} image manifest")
            require_digest(item["digest"])
            output[str(arch)] = item
    if set(output) != set(ARCHITECTURES):
        raise ValueError("Release is missing an amd64 or arm64 image")
    return output


def attestation_descriptor(index: dict, image_digest: str) -> dict:
    """Find the attestation manifest connected to an image manifest."""

    matches = []
    for item in index.get("manifests", []):
        labels = item.get("annotations", {})
        if labels.get("vnd.docker.reference.type") != "attestation-manifest":
            continue
        if labels.get("vnd.docker.reference.digest") == image_digest:
            matches.append(item)
    if len(matches) != 1:
        raise ValueError(
            "Exactly one attestation is required per architecture"
        )
    require_digest(matches[0]["digest"])
    return matches[0]


def image_revision(config: dict) -> str | None:
    """Read the source revision recorded in the OCI config labels."""

    labels = config.get("config", {}).get("Labels") or {}
    return labels.get("org.opencontainers.image.revision")


def statement_types(token: str, manifest: dict, image_digest: str) -> set[str]:
    """Verify attestation statements and return their predicate types."""

    kinds: set[str] = set()
    expected = image_digest.partition(":")[2]
    for layer in manifest.get("layers", []):
        if layer.get("mediaType") != "application/vnd.in-toto+json":
            continue
        statement = fetch_object(token, layer["digest"], "blobs")
        subject = statement.get("subject", [])
        if not any(
            x.get("digest", {}).get("sha256") == expected for x in subject
        ):
            raise ValueError("Attestation subject is not the image manifest")
        kind = statement.get("predicateType", "")
        predicate = statement.get("predicate", {})
        if kind == "https://spdx.dev/Document":
            if predicate.get("spdxVersion") != "SPDX-2.3":
                raise ValueError("SPDX 2.3 metadata is missing")
            if not predicate.get("packages"):
                raise ValueError("SPDX has no packages")
        if kind.startswith("https://slsa.dev/provenance/") and not (
            predicate.get("buildType") or predicate.get("buildDefinition")
        ):
            raise ValueError("SLSA build information is missing")
        kinds.add(kind)
    return kinds


def verify_image(token: str, index: dict, commit: str) -> None:
    """Verify per-platform code labels and signed-index attestations."""

    images = platform_manifests(index)
    for arch, image in images.items():
        digest = image["digest"]
        platform = fetch_object(token, digest, "manifests")
        config = fetch_object(token, platform["config"]["digest"], "blobs")
        if image_revision(config) != commit:
            raise ValueError(f"Unexpected source revision on linux/{arch}")
        attestation = attestation_descriptor(index, digest)
        manifest = fetch_object(token, attestation["digest"], "manifests")
        types = statement_types(token, manifest, digest)
        if "https://spdx.dev/Document" not in types:
            raise ValueError(f"SPDX SBOM missing on linux/{arch}")
        if not any(
            x.startswith("https://slsa.dev/provenance/") for x in types
        ):
            raise ValueError(f"SLSA provenance missing on linux/{arch}")
        print(f"PASS linux/{arch}: revision, SPDX, SLSA, subject digest")


def main() -> int:
    """Validate an immutable public image without changing production."""

    args = arguments()
    require_digest(args.digest)
    if not re.fullmatch(r"[0-9a-f]{40}", args.commit):
        raise ValueError("--commit must be an exact Git SHA-1")
    token = token_for_public_image()
    index = fetch_object(token, args.digest, "manifests")
    if index.get("mediaType") not in INDEX_TYPES:
        raise ValueError("The release digest is not an OCI image index")
    verify_image(token, index, args.commit)
    print("RELEASE OCI ATTESTATIONS: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
