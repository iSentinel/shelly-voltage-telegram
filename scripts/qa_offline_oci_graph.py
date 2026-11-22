#!/usr/bin/env python3
"""Inspect a local OCI content graph; not signature or receipt authority."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import json
import os
import re
import stat
from pathlib import Path

MAX_BLOB = 512 * 1024 * 1024
MAX_JSON = 16 * 1024 * 1024
MAX_EXPANDED = 1024 * 1024 * 1024
GZIP_LAYER = "application/vnd.oci.image.layer.v1.tar+gzip"
MANIFEST = "application/vnd.oci.image.manifest.v1+json"
CONFIG = "application/vnd.oci.image.config.v1+json"
INDEX = "application/vnd.oci.image.index.v1+json"
STATEMENT = "application/vnd.in-toto+json"


def load_policy():
    """Reuse the published-image platform and attestation selection rules."""
    path = Path(__file__).with_name("verify-published-image.py")
    spec = importlib.util.spec_from_file_location("oci_graph_policy", path)
    if spec is None or spec.loader is None:
        raise ValueError("GRAPH_POLICY_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


POLICY = load_policy()


def safe_blob(root: Path, descriptor: dict) -> Path:
    """Require exact size and SHA256 for regular, nonlinked blob bytes."""
    name = POLICY.require_digest(descriptor["digest"]).split(":")[1]
    size = descriptor["size"]
    path = root / "blobs" / "sha256" / name
    if type(size) is not int or not 0 <= size <= MAX_BLOB:
        raise ValueError("GRAPH_BLOB_SIZE_INVALID")
    info = path.lstat()
    if (
        path.resolve() != path
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid != os.geteuid()
        or info.st_mode & 0o022
        or info.st_size != size
    ):
        raise ValueError("GRAPH_BLOB_UNSAFE")
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != name:
        raise ValueError("GRAPH_BLOB_HASH_MISMATCH")
    return path


def json_blob(root: Path, descriptor: dict) -> dict:
    """Read a bounded JSON object after checking its descriptor."""
    if not 0 < descriptor["size"] <= MAX_JSON:
        raise ValueError("GRAPH_JSON_SIZE_INVALID")
    data = json.loads(safe_blob(root, descriptor).read_bytes())
    if not isinstance(data, dict):
        raise ValueError("GRAPH_JSON_INVALID")
    return data


def diff_id(root: Path, descriptor: dict) -> str:
    """Check compressed bytes and independently hash the unpacked stream."""
    if descriptor["mediaType"] != GZIP_LAYER:
        raise ValueError("GRAPH_LAYER_TYPE_INVALID")
    digest = hashlib.sha256()
    size = 0
    with gzip.open(safe_blob(root, descriptor), "rb") as stream:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_EXPANDED:
                raise ValueError("GRAPH_LAYER_EXPANDED_LIMIT")
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def parse_image(root: Path, descriptor: dict, arch: str, commit: str) -> dict:
    """Check manifest, config, architecture, revision and every layer."""
    manifest = json_blob(root, descriptor)
    if (
        descriptor["mediaType"] != MANIFEST
        or manifest.get("mediaType") != MANIFEST
    ):
        raise ValueError("GRAPH_MANIFEST_TYPE_INVALID")
    config_desc = manifest["config"]
    if config_desc["mediaType"] != CONFIG:
        raise ValueError("GRAPH_CONFIG_TYPE_INVALID")
    config = json_blob(root, config_desc)
    if (
        config.get("os") != "linux"
        or config.get("architecture") != arch
        or POLICY.image_revision(config) != commit
        or config["rootfs"].get("type") != "layers"
    ):
        raise ValueError("GRAPH_CONFIG_IDENTITY_MISMATCH")
    layers = manifest["layers"]
    if not 0 < len(layers) <= 100:
        raise ValueError("GRAPH_LAYER_COUNT_INVALID")
    actual = [diff_id(root, layer) for layer in layers]
    if actual != config["rootfs"]["diff_ids"]:
        raise ValueError("GRAPH_DIFF_ID_MISMATCH")
    return {
        "manifest": descriptor["digest"],
        "config": config_desc["digest"],
        "diff_ids": actual,
        "layer_count": len(actual),
    }


def statement_kind(statement: dict, digest: str) -> str:
    """Check in-toto subjects and required SPDX/SLSA metadata."""
    if not any(
        s.get("digest", {}).get("sha256") == digest.split(":")[1]
        for s in statement.get("subject", [])
    ):
        raise ValueError("GRAPH_ATTESTATION_SUBJECT_MISMATCH")
    kind = statement["predicateType"]
    predicate = statement["predicate"]
    if kind == "https://spdx.dev/Document":
        if predicate.get("spdxVersion") != "SPDX-2.3" or not predicate.get(
            "packages"
        ):
            raise ValueError("GRAPH_SPDX_INVALID")
    elif kind == "https://slsa.dev/provenance/v1":
        if not predicate.get("buildDefinition", {}).get("buildType"):
            raise ValueError("GRAPH_SLSA_INVALID")
        if not predicate.get("runDetails"):
            raise ValueError("GRAPH_SLSA_INVALID")
    else:
        raise ValueError("GRAPH_ATTESTATION_KIND_INVALID")
    return kind


def parse_attestation(root: Path, descriptor: dict, image: str) -> list:
    """Verify all attestation objects reachable from the signed index."""
    data = json_blob(root, descriptor)
    if (
        descriptor["mediaType"] != MANIFEST
        or data.get("mediaType") != MANIFEST
    ):
        raise ValueError("GRAPH_ATTESTATION_MANIFEST_INVALID")
    json_blob(root, data["config"])
    kinds = []
    for layer in data["layers"]:
        if layer["mediaType"] != STATEMENT:
            raise ValueError("GRAPH_ATTESTATION_LAYER_INVALID")
        kind = statement_kind(json_blob(root, layer), image)
        if (
            layer.get("annotations", {}).get("in-toto.io/predicate-type")
            != kind
        ):
            raise ValueError("GRAPH_ATTESTATION_ANNOTATION_MISMATCH")
        kinds.append(kind)
    if sorted(kinds) != [
        "https://slsa.dev/provenance/v1",
        "https://spdx.dev/Document",
    ]:
        raise ValueError("GRAPH_ATTESTATION_SET_INVALID")
    return sorted(kinds)


def verify(root: Path, digest: str, commit: str) -> dict:
    """Bind every expected reachable object to the externally pinned index."""
    POLICY.require_digest(digest)
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("GRAPH_COMMIT_INVALID")
    if not root.is_absolute() or root.resolve() != root:
        raise ValueError("GRAPH_PATH_INVALID")
    wrapper = root / "index.json"
    if wrapper.is_symlink() or wrapper.stat().st_size > MAX_JSON:
        raise ValueError("GRAPH_INDEX_WRAPPER_INVALID")
    entries = json.loads(wrapper.read_bytes())["manifests"]
    if len(entries) != 1 or entries[0]["digest"] != digest:
        raise ValueError("GRAPH_INDEX_DIGEST_MISMATCH")
    index = json_blob(root, entries[0])
    if index.get("mediaType") != INDEX or len(index["manifests"]) != 4:
        raise ValueError("GRAPH_INDEX_INVALID")
    images = POLICY.platform_manifests(index)
    result = {}
    for arch, desc in images.items():
        image = parse_image(root, desc, arch, commit)
        attestation = POLICY.attestation_descriptor(index, image["manifest"])
        image["attestations"] = parse_attestation(
            root, attestation, image["manifest"]
        )
        result[arch] = image
    return {
        "status": "PASS_QA_OCI_GRAPH_ONLY",
        "digest": digest,
        "commit": commit,
        "images": result,
        "signature_verified": False,
        "runtime_verified": False,
        "release_authorized": False,
        "production_modified": False,
    }


def main() -> int:
    """Report content verification without creating any receipt."""
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--layout", required=True, type=Path)
    cli.add_argument("--digest", required=True)
    cli.add_argument("--commit", required=True)
    args = cli.parse_args()
    try:
        report = verify(args.layout, args.digest, args.commit)
    except (ValueError, OSError, KeyError, TypeError, EOFError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}))
        return 3
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
