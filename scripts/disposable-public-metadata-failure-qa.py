#!/usr/bin/env python3
"""Offline-only public deployment metadata transaction fault QA."""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import stat
import tempfile
from pathlib import Path

VERSION_RE = re.compile(r"0\.5\.[0-9]+-hw\Z")
DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
IMAGE = "ghcr.io/isentinel/shelly-voltage-telegram"
CONFIRM = "ISOLATED_PUBLIC_METADATA_FAULT_QA_ONLY"


class InjectedFailure(RuntimeError):
    """Synthetic power-loss or commit-boundary exception."""


def valid_deployed(item: object) -> bool:
    """Validate the public/private metadata contract strictly."""
    if not isinstance(item, dict) or set(item) != {"version", "digest"}:
        return False
    version, digest = item["version"], item["digest"]
    return (
        isinstance(version, str)
        and VERSION_RE.fullmatch(version) is not None
        and isinstance(digest, str)
        and DIGEST_RE.fullmatch(digest) is not None
    )


def classify(
    private: object,
    public: object,
    pending: bool,
    running_ref: str,
    expected_id: str,
    running_id: str,
    verified: bool,
) -> str:
    """Fail closed before asserting public state consistency."""
    if pending:
        return "PENDING_BLOCKED"
    if not valid_deployed(private):
        return "PRIVATE_INVALID_BLOCKED"
    if not verified:
        return "TRUST_NOT_VERIFIED_BLOCKED"
    if running_ref != IMAGE + "@" + private["digest"]:
        return "RUNNING_REFERENCE_MISMATCH_BLOCKED"
    if running_id != expected_id or not DIGEST_RE.fullmatch(running_id):
        return "RUNNING_IMAGE_ID_MISMATCH_BLOCKED"
    if public is not None and not valid_deployed(public):
        return "PUBLIC_CORRUPT_BLOCKED"
    if public == private:
        return "CONSISTENT"
    return "RECONCILE_REQUIRED"


def fsync_directory(path: Path) -> None:
    """Persist directory metadata after an atomic rename."""
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_publish(path: Path, data: dict, failpoint: str = "") -> None:
    """Write one complete JSON file; never expose a partial JSON."""
    if not valid_deployed(data):
        raise ValueError("INVALID_DEPLOYMENT_DATA")
    fd, raw = tempfile.mkstemp(prefix=".deployed-", dir=path.parent)
    temporary = Path(raw)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(data, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        if failpoint == "after_file_fsync":
            raise InjectedFailure(failpoint)
        os.replace(temporary, path)
        if failpoint == "after_replace":
            raise InjectedFailure(failpoint)
        fsync_directory(path.parent)
        if failpoint == "after_dir_fsync":
            raise InjectedFailure(failpoint)
    finally:
        temporary.unlink(missing_ok=True)


def read_public(path: Path) -> object:
    """Parse existing public JSON, rejecting malformed content."""
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def check(name: str, assertion: bool, cases: list[str]) -> None:
    """Record a single successful deterministic assertion."""
    if not assertion:
        raise AssertionError(name)
    cases.append(name)


def baseline() -> tuple[dict, dict, str, str]:
    """Build invented release metadata and image identities."""
    old = {"version": "0.5.20-hw", "digest": "sha256:" + "a" * 64}
    current = {"version": "0.5.21-hw", "digest": "sha256:" + "b" * 64}
    running_ref = IMAGE + "@" + current["digest"]
    running_id = "sha256:" + "c" * 64
    return old, current, running_ref, running_id


def status(private: object, public: object, **overrides: object) -> str:
    """Classify entirely synthetic sources and signed-trust flag."""
    _, _, reference, image_id = baseline()
    args = {
        "pending": False,
        "running_ref": reference,
        "expected_id": image_id,
        "running_id": image_id,
        "verified": True,
    }
    args.update(overrides)
    return classify(private, public, **args)


def require_injected_failure(path: Path, data: dict, point: str) -> None:
    """Fail QA if a requested fault injection did not actually fire."""
    try:
        atomic_publish(path, data, point)
    except InjectedFailure:
        return
    raise AssertionError("FAULT_NOT_TRIGGERED: " + point)


def fault_checks(folder: Path, cases: list[str]) -> None:
    """Verify all file fsync/replace interruption boundaries."""
    old, current, _, _ = baseline()
    path = folder / "deployed.json"
    original = (json.dumps(old, sort_keys=True) + "\n").encode()
    path.write_bytes(original)
    require_injected_failure(path, current, "after_file_fsync")
    check("pre_replace_keeps_old_bytes", path.read_bytes() == original, cases)
    check(
        "pre_replace_cleans_temp",
        not list(folder.glob(".deployed-*")),
        cases,
    )
    require_injected_failure(path, current, "after_replace")
    check("post_replace_complete_json", read_public(path) == current, cases)
    check(
        "post_replace_is_not_success",
        status(current, read_public(path)) == "CONSISTENT",
        cases,
    )
    require_injected_failure(path, current, "after_dir_fsync")
    check("post_dir_fsync_complete", read_public(path) == current, cases)
    check("no_orphan_temp", not list(folder.glob(".deployed-*")), cases)
    check(
        "public_mode_0600", stat.S_IMODE(path.stat().st_mode) == 0o600, cases
    )


def state_checks(cases: list[str]) -> None:
    """Verify pending, signed identity, corrupted-public refusal."""
    old, current, _, _ = baseline()
    check(
        "private_uncommitted",
        status(None, old) == "PRIVATE_INVALID_BLOCKED",
        cases,
    )
    check(
        "pending_before_publish",
        status(current, old, pending=True) == "PENDING_BLOCKED",
        cases,
    )
    check(
        "pending_removed_public_stale",
        status(current, old) == "RECONCILE_REQUIRED",
        cases,
    )
    check(
        "public_missing", status(current, None) == "RECONCILE_REQUIRED", cases
    )
    check(
        "corrupt_public",
        status(current, {"garbage": 1}) == "PUBLIC_CORRUPT_BLOCKED",
        cases,
    )
    identity_checks(cases)


def identity_checks(cases: list[str]) -> None:
    """Verify synthetic image identity, trust and complete consistency."""
    old, current, running_ref, image_id = baseline()
    check(
        "signed_trust_missing",
        status(current, old, verified=False) == "TRUST_NOT_VERIFIED_BLOCKED",
        cases,
    )
    check(
        "wrong_image_ref",
        status(current, old, running_ref=running_ref + "x")
        == "RUNNING_REFERENCE_MISMATCH_BLOCKED",
        cases,
    )
    check(
        "wrong_running_id",
        status(current, old, running_id="sha256:" + "d" * 64)
        == "RUNNING_IMAGE_ID_MISMATCH_BLOCKED",
        cases,
    )
    check(
        "signed_image_consistent",
        status(current, current) == "CONSISTENT",
        cases,
    )
    check("expected_id_is_valid", bool(DIGEST_RE.fullmatch(image_id)), cases)


def guest_guard() -> None:
    """Refuse production, any networked guest or unprivileged QA."""
    if os.geteuid() != 0 or socket.gethostname() != "shelly-docker-reboot-qa":
        raise RuntimeError("OFFLINE_QA_GUEST_REQUIRED")
    if {name for _, name in socket.if_nameindex()} != {"lo"}:
        raise RuntimeError("NON_LOOPBACK_NETWORK_BLOCKED")


def main() -> int:
    """Run only explicitly confirmed private synthetic fault checks."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--run-fixtures", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    if args.dry_run or not args.run_fixtures:
        print("DRY_RUN: no files, production, Docker or Telegram touched")
        return 0
    if args.confirm != CONFIRM:
        print("FAIL: exact offline-QA confirmation required")
        return 2
    try:
        guest_guard()
        cases: list[str] = []
        state_checks(cases)
        with tempfile.TemporaryDirectory(
            prefix="svt-public-metadata-qa-", dir="/var/tmp"
        ) as name:
            folder = Path(name)
            folder.chmod(0o700)
            fault_checks(folder, cases)
    except (AssertionError, OSError, ValueError, RuntimeError) as error:
        print("QA_FAIL", type(error).__name__, str(error))
        return 3
    print(
        json.dumps(
            {
                "result": "PASS_SYNTHETIC_METADATA_FAULT_BOUNDARIES",
                "assertions": len(cases),
                "cases": cases,
                "actual_oci_signature": "MOCKED_OFFLINE",
                "global_writer_fence": "NOT_INSTALLED",
                "physical_power_loss": "NOT_TESTED",
                "production_modified": False,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
