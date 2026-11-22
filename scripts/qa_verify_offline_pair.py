#!/usr/bin/env python3
"""Verify an offline release pair for QA evidence, never stage a receipt."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path

import qa_offline_oci_graph as graph


def load_pair():
    """Reuse exact release identity and source archive policy."""
    path = Path(__file__).with_name("verify-signed-release-pair.py")
    spec = importlib.util.spec_from_file_location("offline_pair_policy", path)
    if spec is None or spec.loader is None:
        raise ValueError("OFFLINE_PAIR_POLICY_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PAIR = load_pair()
SOURCE = PAIR.SOURCE


def sha256(path: Path) -> str:
    """Hash exact bytes without loading large artifacts."""
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify_signature(args, workflow, operation, bundle, artifact):
    """Require explicit root and exact workflow, ref, issuer and commit."""
    ref = f"refs/tags/v{args.version}"
    identity = (
        "https://github.com/iSentinel/shelly-voltage-telegram/"
        f".github/workflows/{workflow}.yml@{ref}"
    )
    command = [
        SOURCE.trusted_cosign(),
        operation,
        "--trusted-root",
        str(args.root),
        "--bundle",
        str(bundle),
        "--certificate-identity",
        identity,
        "--certificate-oidc-issuer",
        SOURCE.ISSUER,
        "--certificate-github-workflow-ref",
        ref,
        "--certificate-github-workflow-repository",
        "iSentinel/shelly-voltage-telegram",
        "--certificate-github-workflow-sha",
        args.commit,
    ]
    if operation == "verify-blob-attestation":
        command += [
            "--type",
            "https://sigstore.dev/cosign/sign/v1",
            "--check-claims=true",
        ]
    result = subprocess.run(
        command + [str(artifact)], capture_output=True, timeout=90, check=False
    )
    if result.returncode:
        raise ValueError(f"OFFLINE_{workflow.upper()}_SIGNATURE_UNVERIFIED")


def verify_inputs(args):
    """Check root pin plus the original bounded source and OCI inputs."""
    PAIR.verify_inputs(args)
    SOURCE.validate_paths(args)
    for path, bound in (
        (args.root, 1024 * 1024),
        (args.index, 16 * 1024 * 1024),
        (args.oci_bundle, 512 * 1024),
    ):
        SOURCE.safe_file(path, bound)
    if sha256(args.root) != args.root_sha256:
        raise ValueError("OFFLINE_ROOT_PIN_MISMATCH")
    if "sha256:" + sha256(args.index) != args.digest:
        raise ValueError("OFFLINE_INDEX_DIGEST_MISMATCH")
    SOURCE.verify_archive_versions(args)
    return SOURCE.verify_checksums(args)


def inspect_local(info: dict, expected: dict, arch: str, commit: str):
    """Match immutable config identity and ordered layers, not RepoDigests."""
    if (
        info.get("Id") != expected["config"]
        or info.get("Architecture") != arch
        or info.get("Os") != "linux"
        or info.get("RootFS", {}).get("Type") != "layers"
        or info.get("RootFS", {}).get("Layers") != expected["diff_ids"]
        or info.get("Config", {})
        .get("Labels", {})
        .get("org.opencontainers.image.revision")
        != commit
    ):
        raise ValueError("OFFLINE_LOCAL_IMAGE_MISMATCH")


def verify(args):
    """Return non-authorizing evidence after both actual signature checks."""
    source_sha = verify_inputs(args)
    verify_signature(args, "release", "verify-blob", args.bundle, args.archive)
    verify_signature(
        args, "image", "verify-blob-attestation", args.oci_bundle, args.index
    )
    report = graph.verify(args.layout, args.digest, args.commit)
    image = report["images"][args.arch]
    result = subprocess.run(
        ["docker", "image", "inspect", image["config"]],
        capture_output=True,
        timeout=30,
        check=True,
    )
    info = json.loads(result.stdout)
    if not isinstance(info, list) or len(info) != 1:
        raise ValueError("OFFLINE_LOCAL_IMAGE_INSPECT_INVALID")
    inspect_local(info[0], image, args.arch, args.commit)
    return {
        "status": "PASS_QA_OFFLINE_PAIR_CONTENT_ONLY",
        "version": args.version,
        "commit": args.commit,
        "digest": args.digest,
        "source_sha256": source_sha,
        "root_sha256": args.root_sha256,
        "local_config_id": image["config"],
        "local_arch": args.arch,
        "local_diff_id_count": len(image["diff_ids"]),
        "root_provenance_independently_verified": False,
        "runtime_verified": False,
        "receipt_staged": False,
        "release_authorized": False,
        "production_modified": False,
    }


def arguments():
    """Accept only explicit proof paths and pinned release identity."""
    cli = argparse.ArgumentParser(description=__doc__)
    for name in ("version", "commit", "digest", "root-sha256"):
        cli.add_argument("--" + name, required=True)
    for name in (
        "archive",
        "bundle",
        "sha256sums",
        "root",
        "index",
        "oci-bundle",
        "layout",
    ):
        cli.add_argument("--" + name, required=True, type=Path)
    cli.add_argument("--arch", choices=("amd64", "arm64"), required=True)
    return cli.parse_args()


def main():
    """Report failure without modifying Docker or creating a receipt."""
    try:
        report = verify(arguments())
    except (
        ValueError,
        OSError,
        KeyError,
        TypeError,
        subprocess.SubprocessError,
    ) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}))
        return 3
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
