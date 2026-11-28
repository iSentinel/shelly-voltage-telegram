#!/usr/bin/env python3
"""Prepare a fixed offline QA build context without executing a build."""

import argparse
import hashlib
import io
import json
import runpy
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/qa/report-worker-apt-review"
STAGE = runpy.run_path(str(ROOT / "scripts/qa-stage-report-build-inputs.py"))
STAGING_SHA = (
    "56d39da62e3e8d0e65087b9f67593e309cb05bcb6976eab3ccad613b416165f5"
)
BASE_MANIFESTS = {
    "amd64": (
        "aaf08c5490d52cd92bc27142a5215b1e47355a5eaf9adfad2a0fbc022d0b5490"
    ),
    "arm64": (
        "7b58e4266b99c869d2225c050345f7c7fec255d9e3baeca19ea7de9411411c91"
    ),
}


def sha(data):
    """Return a SHA256 content identity."""
    return hashlib.sha256(data).hexdigest()


def load_bundle(path, entry, arch):
    """Read only the exact reviewed archive and its declared members."""
    raw = STAGE["checked_bytes"](path, entry["archive_sha256"], entry["bytes"])
    files = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as tar:
        for item in tar:
            if not item.isfile() or item.name in files:
                raise ValueError("Invalid archive member")
            files[item.name] = tar.extractfile(item).read()
    manifest_raw = files["input-manifest.json"]
    if sha(manifest_raw) != entry["manifest_sha256"]:
        raise ValueError("Bundle manifest mismatch")
    manifest = json.loads(manifest_raw)
    if manifest["platform"] != "linux/" + arch:
        raise ValueError("Bundle platform mismatch")
    if manifest["build_authorized"] is not False:
        raise ValueError("Unexpected bundle authority")
    hashes = {
        n: sha(v) for n, v in files.items() if n != "input-manifest.json"
    }
    if hashes != manifest["files"] or len(hashes) != 51:
        raise ValueError("Bundle file hashes mismatch")
    return files


def pinned_review():
    """Read fixed review anchors rather than mutable build assumptions."""
    read = STAGE["checked_bytes"]
    staging = json.loads(read(EVIDENCE / "staging-review.json", STAGING_SHA))
    review = json.loads(
        read(EVIDENCE / "build-input-review.json", STAGE["INPUT_SHA"])
    )
    evidence = review["evidence_sha256"]
    documents = {}
    for name in (
        "base-inventory.json",
        "upgrade-artifact-review.json",
        "static-relationship-review.json",
    ):
        path = EVIDENCE / name
        documents[name] = json.loads(
            read(path, evidence[str(path.relative_to(ROOT))])
        )
    return staging, review, documents


def expected_state(arch, entry, review, docs):
    """Derive exact package identities for the post-build check."""
    base = docs["base-inventory.json"][arch]
    artifacts = docs["upgrade-artifact-review.json"]
    by_hash = {x["sha256"]: x for x in artifacts["artifacts"]}
    debian = {
        x["name"]: [x["version"], x["architecture"]] for x in base["packages"]
    }
    target = review["platforms"][arch]
    for row in target["deb_artifacts"]:
        item = by_hash[row["sha256"]]
        debian[item["name"]] = [item["version"], item["architecture"]]
    versions = docs["static-relationship-review.json"]["platforms"][arch][
        "expected_versions"
    ]
    if {n: v[0] for n, v in debian.items()} != versions or len(debian) != 90:
        raise ValueError("Expected Debian set mismatch")
    python = {x["name"]: x["version"] for x in target["python_wheels"]}
    python["pip"] = "26.2.2"
    if len(python) != 9:
        raise ValueError("Expected Python set mismatch")
    return {
        "base_status_sha256": base["dpkg_status_sha256"],
        "input_manifest_sha256": entry["manifest_sha256"],
        "machine": {"amd64": "x86_64", "arm64": "aarch64"}[arch],
        "debian": debian,
        "python": python,
    }


def prepare(arch, bundle, output):
    """Create the closed context with a platform-specific recipe."""
    staging, review, docs = pinned_review()
    entry = staging[arch]
    files = load_bundle(bundle, entry, arch)
    expected = expected_state(arch, entry, review, docs)
    template = (EVIDENCE / "offline-Dockerfile.template").read_text()
    if template.count("BASE_MANIFEST") != 1:
        raise ValueError("Invalid recipe template")
    recipe = template.replace(
        "BASE_MANIFEST", "sha256:" + BASE_MANIFESTS[arch]
    )
    files["Dockerfile"] = recipe.encode()
    files["verify-build-state.py"] = (
        ROOT / "scripts/qa-verify-offline-build-state.py"
    ).read_bytes()
    files["expected-state.json"] = (
        json.dumps(expected, sort_keys=True, indent=2) + "\n"
    ).encode()
    STAGE["write_archive"](files, output)


def main():
    """Expose context preparation only; never invoke Docker."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arch", choices=tuple(BASE_MANIFESTS), required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        prepare(args.arch, args.bundle, args.output)
    except (OSError, ValueError, KeyError, tarfile.TarError) as exc:
        parser.exit(1, f"QA BUILD CONTEXT: FAIL: {exc}\n")
    print(f"QA BUILD CONTEXT: PASS {args.arch}; build not executed")


if __name__ == "__main__":
    main()
