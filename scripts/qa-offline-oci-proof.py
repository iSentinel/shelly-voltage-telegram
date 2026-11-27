#!/usr/bin/env python3
"""Verify preserved OCI bytes against a trusted index and local image."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import json
import re
import stat
import subprocess
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "pair_policy", Path(__file__).with_name("qa-report-signed-pair-policy.py")
)
assert SPEC and SPEC.loader
PAIR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PAIR)
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
INDEX = "application/vnd.oci.image.index.v1+json"
MANIFEST = "application/vnd.oci.image.manifest.v1+json"
CONFIG = "application/vnd.oci.image.config.v1+json"
LAYER = "application/vnd.oci.image.layer.v1.tar+gzip"
MAX_LAYER = 512 * 1024 * 1024


def checked_file(path: Path, maximum: int) -> int:
    """Require a bounded root-owned ordinary file in protected parents."""
    PAIR.SUP.require_root_ancestors(path)
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid != 0
        or info.st_mode & 0o022
        or not 0 < info.st_size <= maximum
    ):
        raise ValueError("unsafe OCI evidence file")
    return info.st_size


def raw_json(path: Path, expected: str, size: int | None = None) -> dict:
    """Hash exact original JSON bytes before interpreting their contents."""
    length = checked_file(path, 4 * 1024 * 1024)
    raw = path.read_bytes()
    if "sha256:" + PAIR.digest(raw) != expected or (
        size is not None and length != size
    ):
        raise ValueError("OCI JSON digest or size mismatch")
    return PAIR.object_bytes(raw)


def descriptor(record: dict, media: str) -> tuple[str, int]:
    """Accept only canonical digest, positive size and expected media type."""
    value, size = record.get("digest"), record.get("size")
    if (
        record.get("mediaType") != media
        or not isinstance(value, str)
        or not DIGEST.fullmatch(value)
        or type(size) is not int
        or not 0 < size <= MAX_LAYER
        or "urls" in record
        or "data" in record
    ):
        raise ValueError("unsupported OCI descriptor")
    return value, size


def stream_digest(stream, limit: int) -> str:
    """Bound decompression while hashing the full byte stream."""
    result = hashlib.sha256()
    total = 0
    while block := stream.read(1024 * 1024):
        total += len(block)
        if total > limit:
            raise ValueError("OCI layer exceeds size bound")
        result.update(block)
    return "sha256:" + result.hexdigest()


def layer_diffid(directory: Path, record: dict) -> str:
    """Check compressed blob size/digest and derive its uncompressed DiffID."""
    value, size = descriptor(record, LAYER)
    path = directory / value.removeprefix("sha256:")
    if checked_file(path, MAX_LAYER) != size:
        raise ValueError("OCI layer size mismatch")
    with path.open("rb") as stream:
        if stream_digest(stream, MAX_LAYER) != value:
            raise ValueError("OCI layer digest mismatch")
    with gzip.open(path, "rb") as stream:
        return stream_digest(stream, MAX_LAYER)


def manifest_descriptor(index: dict) -> tuple[str, int]:
    """Select exactly one Linux amd64 image, excluding attestation entries."""
    if index.get("schemaVersion") != 2 or index.get("mediaType") != INDEX:
        raise ValueError("unsupported OCI index")
    matches = [
        item
        for item in index["manifests"]
        if item.get("platform", {}).get("os") == "linux"
        and item.get("platform", {}).get("architecture") == "amd64"
    ]
    if len(matches) != 1:
        raise ValueError("ambiguous OCI platform")
    return descriptor(matches[0], MANIFEST)


def verify_chain(root: Path, expected: str, commit: str) -> dict:
    """Verify signed-index-linked manifest, config and all amd64 layers."""
    if not DIGEST.fullmatch(expected) or not PAIR.SUP.COMMIT.fullmatch(commit):
        raise ValueError("invalid trusted identity")
    directory = root / "blobs"
    index = raw_json(root / "index.json", expected)
    manifest_id, size = manifest_descriptor(index)
    manifest = raw_json(directory / "manifest.json", manifest_id, size)
    if (
        manifest.get("schemaVersion") != 2
        or manifest.get("mediaType") != MANIFEST
    ):
        raise ValueError("unsupported OCI manifest")
    config_id, size = descriptor(manifest["config"], CONFIG)
    config = raw_json(
        directory / config_id.removeprefix("sha256:"), config_id, size
    )
    if (
        config.get("os") != "linux"
        or config.get("architecture") != "amd64"
        or config["config"]["Labels"]["org.opencontainers.image.revision"]
        != commit
        or config["rootfs"]["type"] != "layers"
    ):
        raise ValueError("OCI config identity mismatch")
    layers = manifest["layers"]
    if not isinstance(layers, list) or not 1 <= len(layers) <= 64:
        raise ValueError("unsupported OCI layer count")
    diffids = [layer_diffid(directory, layer) for layer in layers]
    if diffids != config["rootfs"]["diff_ids"]:
        raise ValueError("OCI uncompressed layer identity mismatch")
    return {
        "index_digest": expected,
        "manifest_digest": manifest_id,
        "image_id": config_id,
        "diff_ids": diffids,
        "commit": commit,
    }


def verify_local(proof: dict, image: dict) -> None:
    """Match config ID, platform, revision and ordered rootfs layers."""
    if (
        image.get("Id") != proof["image_id"]
        or image.get("Os") != "linux"
        or image.get("Architecture") != "amd64"
        or PAIR.SUP.image_revision(image) != proof["commit"]
        or image.get("RootFS", {}).get("Layers") != proof["diff_ids"]
    ):
        raise ValueError("local image identity mismatch")


def main() -> int:
    """Review offline image evidence only; never retag or launch an image."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--index-digest", required=True)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args()
    try:
        PAIR.TX.require_qa(args.root)
        with PAIR.TX.exclusive(args.root):
            if PAIR.SUP._pending_under_gate(args.root / "data/qa.sqlite3"):
                raise ValueError("update pending")
            proof = verify_chain(args.root, args.index_digest, args.commit)
            verify_local(proof, PAIR.inspect_image(proof["image_id"]))
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        EOFError,
        subprocess.SubprocessError,
        PAIR.TX.WriterFenceError,
    ) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": type(exc).__name__}))
        return 3
    print(
        json.dumps(
            {
                **proof,
                "status": "PASS_OFFLINE_OCI_BYTES_ONLY",
                "signatures_reverified": False,
                "startup_authorized": False,
                "deployment_authorized": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
