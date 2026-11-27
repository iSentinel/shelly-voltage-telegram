#!/usr/bin/env python3
"""Review an installed signed supervisor without changing service state."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import stat
import sys
import tarfile
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "signed_forward", Path(__file__).with_name("signed-forward-cutover.py")
)
assert SPEC and SPEC.loader
CUT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = CUT
SPEC.loader.exec_module(CUT)


def sha(path: Path) -> str:
    """Hash an existing protected file."""
    CUT.safe_path(path)
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def source_inputs(paths: CUT.Layout, pair: dict, work: Path) -> int:
    """Compare signed build and startup inputs with installed bytes."""
    archive = CUT.retained_archive(work, pair["version"])
    inputs = CUT.archived_monitor_inputs(
        archive, pair["version"], pair["source_sha256"]
    )
    startup = CUT.ROLLBACK_SUPERVISOR_FILES | {"scripts/start-compose.sh"}
    prefix = f"shelly-voltage-telegram-{pair['version']}/"
    with tarfile.open(archive, "r:gz") as source:
        for name in startup:
            members = [m for m in source if m.name == prefix + name]
            if len(members) != 1 or not members[0].isfile():
                raise ValueError("ambiguous signed startup input")
            with source.extractfile(members[0]) as stream:
                inputs[name] = hashlib.file_digest(
                    stream, "sha256"
                ).hexdigest()
    for name, expected in inputs.items():
        if sha(paths.source / name) != expected:
            raise ValueError("installed signed input changed")
    return len(inputs)


def owner_held(path: Path) -> bool:
    """Probe an existing owner inode without creating or writing it."""
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid != 10001
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        raise ValueError("unsafe writer lock")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        actual = os.fstat(fd)
        if (info.st_dev, info.st_ino) != (actual.st_dev, actual.st_ino):
            raise ValueError("writer lock changed")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def runtime_identity() -> list[str]:
    """Read only container and image identity, not the private environment."""
    result = CUT.run(
        "docker",
        "inspect",
        "--format",
        "{{.Id}}|{{.Image}}|{{.State.StartedAt}}",
        CUT.CONTAINER,
    ).split("|")
    if len(result) != 3 or not all(result):
        raise ValueError("invalid runtime identity")
    return result


def check_runtime(paths: CUT.Layout, pair: dict, identity: list[str]) -> None:
    """Require runtime mounts, signed pin and actual local revision."""
    CUT.verify_current_mounts(paths, True)
    image = json.loads(
        CUT.run(
            "docker",
            "image",
            "inspect",
            "--format",
            "{{json .RepoDigests}}",
            identity[1],
        )
    )
    revision = CUT.run(
        "docker",
        "image",
        "inspect",
        "--format",
        '{{index .Config.Labels "org.opencontainers.image.revision"}}',
        identity[1],
    )
    if (
        CUT.image_pin(pair["digest"]) not in image
        or revision != pair["source_commit"]
        or pair.get("commit") != pair["source_commit"]
        or pair.get("archive_sha256") != pair["source_sha256"]
        or pair.get("pair_status") != "PASS_SIGNED_SOURCE_OCI_PAIR_ONLY"
        or any(
            pair.get(k) is not True
            for k in (
                "source_signature_verified",
                "oci_signature_verified",
                "oci_attestations_verified",
            )
        )
    ):
        raise ValueError("installed runtime pair mismatch")


def file_identity(path: Path) -> dict:
    """Record an ordinary single-linked inode without opening SQLite."""
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("unsafe installed evidence inode")
    return {"device": info.st_dev, "inode": info.st_ino}


def evidence_binding(paths: CUT.Layout, work: Path) -> dict:
    """Bind exact existing journal, receipt and live inode identities."""
    return {
        "receipt_sha256": sha(paths.state / "deployed.json"),
        "journal_sha256": sha(work / "transaction.json"),
        "database_identity": file_identity(paths.database),
        "writer_lock_identity": file_identity(paths.fence / ".svt-owner.lock"),
    }


def check_stable(paths, work, pair, identity, binding):
    """Reject identity drift and loss of the observed writer lease."""
    if (
        CUT.installed_runtime(paths, True)[0] != pair
        or runtime_identity() != identity
        or CUT.installed_transaction(paths, pair) != work
        or evidence_binding(paths, work) != binding
        or not owner_held(paths.fence / ".svt-owner.lock")
    ):
        raise ValueError("installed runtime changed during review")


def check_reload():
    """Require both startup units to reflect their on-disk definitions."""
    for unit in (CUT.SUPERVISOR, CUT.MONITOR):
        if (
            CUT.run(
                "systemctl", "show", "-p", "NeedDaemonReload", "--value", unit
            )
            != "no"
        ):
            raise ValueError("unit reload pending")


def review(paths: CUT.Layout) -> dict:
    """Bracket read-only checks; do not authorize any cutover or restart."""
    identity = runtime_identity()
    pair, _, supervised = CUT.installed_runtime(paths, True)
    if not supervised or CUT.state_of(CUT.MONITOR) != "inactive":
        raise ValueError("startup owner not exclusive")
    work = CUT.installed_transaction(paths, pair)
    binding = evidence_binding(paths, work)
    count = source_inputs(paths, pair, work)
    check_runtime(paths, pair, identity)
    if not owner_held(paths.fence / ".svt-owner.lock"):
        raise ValueError("live writer lease unconfirmed")
    check_reload()
    try:
        CUT.public_receipt_preflight(paths)
    except (OSError, ValueError):
        public = False
    else:
        public = True
    check_stable(paths, work, pair, identity, binding)
    return {
        "status": "PASS_INSTALLED_SUPERVISOR_REVIEW_ONLY",
        "version": pair["version"],
        "container_id": identity[0],
        "image_id": identity[1],
        "started_at": identity[2],
        **binding,
        "fault_attribution_verified": False,
        "incident_hold_verified": False,
        "signed_inputs_match": count,
        "writer_lease_held": True,
        "public_receipt_preflight": public,
        "signatures_reverified": False,
        "deployment_authorized": False,
        "startup_authorized": False,
    }


def main() -> int:
    """Use fixed installed paths; output only nonsecret review facts."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    try:
        if os.geteuid() != 0:
            raise ValueError("root review required")
        result = review(CUT.Layout())
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}))
        return 3
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
