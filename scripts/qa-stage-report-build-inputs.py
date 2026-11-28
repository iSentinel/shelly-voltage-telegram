#!/usr/bin/env python3
"""Stage reviewed Report Worker inputs without building or installing."""

import argparse
import hashlib
import io
import json
import os
import re
import stat
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/qa/report-worker-apt-review"
INPUT_SHA = "ef422ec31a01b3a8fe25f6d3602d9ad2c913900448a16ec4986d90ec2bf0ef2f"
SCOPE_SHA = "a3d979232200f7dad24bb7643eb8b97667d6ff83cc2e0418ed171ddd813549c4"
MAX_FILE = 100 * 1024 * 1024


def checked_bytes(path, digest, size=None):
    """Read a bounded regular file once and verify the bytes to be staged."""
    with os.fdopen(
        os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb"
    ) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_FILE:
            raise ValueError(f"Invalid input file: {path.name}")
        data = stream.read(MAX_FILE + 1)
    if len(data) != info.st_size or (size is not None and len(data) != size):
        raise ValueError(f"Input size mismatch: {path.name}")
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError(f"Input SHA256 mismatch: {path.name}")
    return data


def safe_name(name):
    """Accept only a single ordinary artifact basename."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+~%-]*", name):
        raise ValueError("Invalid artifact filename")
    return name


def review_inputs(scope_path):
    """Bind every referenced evidence file to the fixed review anchor."""
    raw = checked_bytes(EVIDENCE / "build-input-review.json", INPUT_SHA)
    review = json.loads(raw)
    scope = json.loads(checked_bytes(scope_path, SCOPE_SHA))
    if scope["commit"] != review["source_review_commit"]:
        raise ValueError("Source review commit mismatch")
    for name, digest in review["evidence_sha256"].items():
        checked_bytes(ROOT / name, digest)
    artifacts = json.loads(
        checked_bytes(
            EVIDENCE / "upgrade-artifact-review.json",
            review["evidence_sha256"][
                "docs/qa/report-worker-apt-review/upgrade-artifact-review.json"
            ],
        )
    )
    return review, scope, artifacts


def runtime_files(scope):
    """Check all 29 build inputs and stage only the 26 runtime source files."""
    inputs = scope["runtime_inputs"]
    expected = {p.name for p in ROOT.glob("*.py")}
    expected.update(
        ("VERSION", "Dockerfile", ".dockerignore", "requirements.txt")
    )
    if set(inputs) != expected or len(inputs) != 29:
        raise ValueError("Runtime input set mismatch")
    output = {}
    for name, digest in sorted(inputs.items()):
        data = checked_bytes(ROOT / name, digest)
        if name.endswith(".py") or name == "VERSION":
            output["runtime/" + name] = data
    if len(output) != 26:
        raise ValueError("Runtime source count mismatch")
    return output


def dependency_files(target, artifacts, deb_dir, wheel_dir, arch):
    """Stage platform-specific artifacts with exact reviewed content."""
    by_hash = {row["sha256"]: row for row in artifacts["artifacts"]}
    output = {}
    for row in target["deb_artifacts"]:
        item = by_hash[row["sha256"]]
        if (item["name"], item["version"]) != (row["name"], row["version"]):
            raise ValueError("DEB identity mismatch")
        if item["architecture"] not in (arch, "all"):
            raise ValueError("DEB architecture mismatch")
        name = safe_name(Path(item["filename"]).name)
        output["debs/" + name] = checked_bytes(
            deb_dir / name, item["sha256"], item["size"]
        )
    for item in target["python_wheels"]:
        name = safe_name(item["filename"])
        output["wheels/" + name] = checked_bytes(
            wheel_dir / name, item["sha256"], item["size"]
        )
    if len(output) != 24:
        raise ValueError("Dependency artifact count mismatch")
    return output


def make_bundle(args):
    """Collect a closed, review-only input set for the selected platform."""
    review, scope, artifacts = review_inputs(args.scope)
    target = review["platforms"][args.arch]
    files = runtime_files(scope)
    files.update(
        dependency_files(target, artifacts, args.debs, args.wheels, args.arch)
    )
    lock = f"docs/qa/report-worker-wheel-review/{args.arch}.requirements.txt"
    files["requirements.lock"] = checked_bytes(
        ROOT / lock, review["evidence_sha256"][lock]
    )
    manifest = {
        "status": "QA_INPUT_BUNDLE_ONLY",
        "platform": "linux/" + args.arch,
        "base_index": review["base_index"],
        "base_layers": target["base_layers"],
        "source_commit": scope["commit"],
        "input_review_sha256": INPUT_SHA,
        "source_scope_sha256": SCOPE_SHA,
        "files": {
            name: hashlib.sha256(data).hexdigest()
            for name, data in sorted(files.items())
        },
        "freshness_recheck_required": True,
        "build_authorized": False,
        "deployment_authorized": False,
    }
    files["input-manifest.json"] = (
        json.dumps(manifest, sort_keys=True, indent=2) + "\n"
    ).encode()
    return files


def write_archive(files, output):
    """Publish a deterministic tar only after completion; never overwrite."""
    with tempfile.NamedTemporaryFile(
        dir=output.parent, prefix=".qa-input-"
    ) as temp:
        with tarfile.open(
            fileobj=temp, mode="w", format=tarfile.PAX_FORMAT
        ) as tar:
            for name, data in sorted(files.items()):
                item = tarfile.TarInfo(name)
                item.size = len(data)
                item.mode = 0o644
                item.uid = item.gid = item.mtime = 0
                tar.addfile(item, io.BytesIO(data))
        temp.flush()
        os.fsync(temp.fileno())
        os.link(temp.name, output)


def main():
    """Run the non-installing QA bundle preparation CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arch", choices=("amd64", "arm64"), required=True)
    for name in ("scope", "debs", "wheels", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    try:
        files = make_bundle(args)
        write_archive(files, args.output)
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"QA INPUT BUNDLE: FAIL: {exc}\n")
    print(f"QA INPUT BUNDLE: PASS {args.arch}, {len(files)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
