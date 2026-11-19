#!/usr/bin/env bash
# Root-owned, offline signed-pair receipt policy rehearsal on LXC114 only.
set -euo pipefail
if [[ $# -eq 1 ]]; then
    if [[ "$1" == -h || "$1" == --help ]]; then
        echo "Usage: bash tests/qa_supervisor_paired_receipt_runtime.sh"
        echo "Root-only on disconnected QA LXC114, never production."
        exit 0
    fi
fi
[[ $# -eq 0 && "$(hostname)" == shelly-docker-reboot-qa &&
   "$EUID" -eq 0 ]] || {
    echo "FAIL: DISPOSABLE_ROOT_QA_ONLY" >&2
    exit 3
}
cd "$(dirname "$0")/.."
python3 - <<'PYQA'
"""Prove root-owned release files and archive parity with no network."""
import hashlib
import importlib.util
import json
import shutil
from pathlib import Path

path = Path("scripts/supervised-container.py")
spec = importlib.util.spec_from_file_location("qa_supervisor", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

root = Path("/var/lib/svt-qa-paired-receipt-20261119")
if root.exists() or root.is_symlink():
    raise SystemExit("FAIL: preexisting QA pair fixture")
root.mkdir(mode=0o700)
try:
    source = root / "source"
    signed = root / "signed-source"
    source.mkdir(mode=0o700)
    signed.mkdir(mode=0o700)
    version = "0.5.26-hw"
    commit = "a" * 40
    digest = "sha256:" + "b" * 64
    release = signed / f"shelly-voltage-telegram-{version}.tar.gz"
    release.write_bytes(b"synthetic-qa-source-not-signed")
    release.chmod(0o600)
    version_file = source / "RELEASE_VERSION"
    version_file.write_text(version + "\n")
    version_file.chmod(0o644)
    receipt = source / "deployed.json"
    record = {
        "version": version,
        "digest": digest,
        "source_commit": commit,
        "source_sha256": hashlib.sha256(release.read_bytes()).hexdigest(),
        "transaction_token": "qa-paired-receipt-20261119",
        "pair_status": "PASS_SIGNED_SOURCE_OCI_PAIR_ONLY",
        "source_signature_verified": True,
        "oci_signature_verified": True,
        "oci_attestations_verified": True,
    }

    def write_receipt():
        receipt.write_text(json.dumps(record))
        receipt.chmod(0o600)

    def check():
        return module.verify_receipt(
            receipt, version_file, require_pair=True,
            image_revision=commit, source_archive_dir=signed
        )

    def reject(expected):
        try:
            check()
        except module.BootBlocked as exc:
            if expected not in str(exc):
                raise AssertionError(f"unexpected refusal: {exc}") from exc
            return
        raise AssertionError(f"unsafe receipt accepted: {expected}")

    write_receipt()
    assert check() == module.IMAGE + digest
    print("REAL_ROOT_PAIR_RECEIPT_AND_SOURCE_SHA256_PASS")
    release.write_bytes(b"modified-qa-source-after-receipt")
    reject("SOURCE_ARCHIVE_MISMATCH")
    print("MODIFIED_SOURCE_ARCHIVE_BLOCKED")
    release.write_bytes(b"synthetic-qa-source-not-signed")
    release.chmod(0o600)
    record["source_commit"] = "d" * 40
    write_receipt()
    reject("OCI_REVISION_MISMATCH")
    record["source_commit"] = commit
    record["source_signature_verified"] = 1
    write_receipt()
    reject("PROOF_INCOMPLETE")
    record["source_signature_verified"] = True
    write_receipt()
    receipt.chmod(0o644)
    reject("SIGNED_RECEIPT_UNSAFE")
    receipt.chmod(0o600)
    release.chmod(0o666)
    reject("SOURCE_ARCHIVE_UNSAFE")
    release.chmod(0o600)
    receipt.unlink()
    receipt.symlink_to(version_file)
    reject("SIGNED_RECEIPT_UNSAFE")
    receipt.unlink()
    write_receipt()
    release.unlink()
    release.symlink_to(version_file)
    reject("SOURCE_ARCHIVE_UNSAFE")
    release.unlink()
    release.write_bytes(b"synthetic-qa-source-not-signed")
    release.chmod(0o600)
    record.pop("source_sha256")
    write_receipt()
    reject("SOURCE_SHA256_INVALID")
    print("REAL_ROOT_PAIR_NEGATIVE_GATE_PASS")
finally:
    shutil.rmtree(root)
    assert not root.exists()
    print("QA_ROOT_PAIRED_RECEIPT_CLEANUP_PASS")
PYQA
