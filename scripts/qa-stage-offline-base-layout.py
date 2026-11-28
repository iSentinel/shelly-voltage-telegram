#!/usr/bin/env python3
"""Stage exact reviewed base OCI blobs without loading or running images."""

import argparse
import gzip
import hashlib
import io
import json
import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGE = runpy.run_path(str(ROOT / "scripts/qa-stage-report-build-inputs.py"))
PREPARE = runpy.run_path(
    str(ROOT / "scripts/qa-prepare-offline-build-context.py")
)
BASE_INDEX = "2d44738ebf4ce5f63b9d9ea871e9c7cfdc0426f7b0c97cac915fd88ded1d86bb"


def sha(data):
    """Return SHA256 of the exact input bytes."""
    return hashlib.sha256(data).hexdigest()


def add_blob(files, source, descriptor):
    """Read a bounded digest-addressed OCI blob and retain original bytes."""
    digest = descriptor["digest"]
    if not digest.startswith("sha256:") or len(digest) != 71:
        raise ValueError("Invalid OCI digest")
    key = digest[7:]
    raw = STAGE["checked_bytes"](source, key, descriptor["size"])
    files["blobs/sha256/" + key] = raw
    return raw


def platform_descriptor(index, arch):
    """Require the reviewed platform manifest, not a mutable tag."""
    matches = [
        row
        for row in index["manifests"]
        if row.get("platform", {}).get("os") == "linux"
        and row["platform"].get("architecture") == arch
    ]
    expected = "sha256:" + PREPARE["BASE_MANIFESTS"][arch]
    if len(matches) != 1 or matches[0]["digest"] != expected:
        raise ValueError("Platform descriptor mismatch")
    return matches[0]


def check_layer(raw, diff_id):
    """Verify both compressed blob identity and uncompressed DiffID."""
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
        digest = hashlib.sha256()
        total = 0
        while chunk := stream.read(1024 * 1024):
            total += len(chunk)
            if total > 512 * 1024 * 1024:
                raise ValueError("Uncompressed layer exceeds limit")
            digest.update(chunk)
    if "sha256:" + digest.hexdigest() != diff_id:
        raise ValueError("Layer DiffID mismatch")


def layout_files(source, arch):
    """Construct a closed single-platform OCI layout from retained bytes."""
    raw = STAGE["checked_bytes"](source / "index.json", BASE_INDEX)
    descriptor = platform_descriptor(json.loads(raw), arch)
    files = {}
    manifest = json.loads(
        add_blob(files, source / f"{arch}-manifest.json", descriptor)
    )
    config = json.loads(
        add_blob(files, source / f"{arch}-config.json", manifest["config"])
    )
    if config["os"] != "linux" or config["architecture"] != arch:
        raise ValueError("Base config platform mismatch")
    layers, diffs = manifest["layers"], config["rootfs"]["diff_ids"]
    if len(layers) != 4 or len(diffs) != 4:
        raise ValueError("Unexpected layer count")
    for i, (layer, diff) in enumerate(zip(layers, diffs, strict=True)):
        if layer["mediaType"] != "application/vnd.oci.image.layer.v1.tar+gzip":
            raise ValueError("Unexpected layer media type")
        payload = add_blob(files, source / f"{arch}-{i}.tar.gz", layer)
        check_layer(payload, diff)
    files["oci-layout"] = b'{"imageLayoutVersion":"1.0.0"}\n'
    index = {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.index.v1+json",
        "manifests": [descriptor],
    }
    files["index.json"] = (
        json.dumps(index, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    return files


def main():
    """Prepare only a deterministic OCI layout archive."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arch", choices=("amd64", "arm64"), required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        files = layout_files(args.source, args.arch)
        STAGE["write_archive"](files, args.output)
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"QA BASE LAYOUT: FAIL: {exc}\n")
    print(f"QA BASE LAYOUT: PASS {args.arch}; no image loaded")


if __name__ == "__main__":
    main()
