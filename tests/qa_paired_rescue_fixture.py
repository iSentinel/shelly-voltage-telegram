#!/usr/bin/env python3
"""Create a private, synthetic old/new rollback pair for LXC114 QA."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import tarfile
from pathlib import Path

ALLOWED = Path("/var/lib/svt-qa-paired-proof-20261119")
OLD = "0.5.24-hw"
NEW = "0.5.25-hw"


def checksum(path: Path) -> None:
    """Bind a disposable fixture file to its exact absolute pathname."""
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    Path(str(path) + ".sha256").write_text(f"{digest}  {path}\n")


def write_source(folder: Path) -> None:
    """Write a minimal, checksummed synthetic source version bundle."""
    folder.mkdir(mode=0o700)
    (folder / "manifest.env").write_text(
        f"from_version={OLD}\nto_version={NEW}\n"
    )
    archive = folder / "source.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        for marker in ("VERSION", "RELEASE_VERSION"):
            data = (OLD + "\n").encode("ascii")
            member = tarfile.TarInfo("./" + marker)
            member.size = len(data)
            bundle.addfile(member, io.BytesIO(data))
    checksum(archive)
    backup = folder / "voltage-123.sqlite3.gz"
    backup.write_bytes(b"offline-qa-only")
    checksum(backup)


def write_signed(folder: Path) -> None:
    """Create a root-private old synthetic digest with a matching pin."""
    folder.mkdir(mode=0o700)
    digest = "sha256:" + "a" * 64
    receipt = folder / "deployed.json"
    override = folder / "override.yaml"
    receipt.write_text(json.dumps({"version": OLD, "digest": digest}))
    override.write_text(
        "services:\n  shelly-voltage:\n"
        "    image: ghcr.io/isentinel/shelly-voltage-telegram@" + digest + "\n"
    )
    receipt.chmod(0o600)
    override.chmod(0o600)
    backup = folder / "voltage-123.sqlite3.gz"
    backup.write_bytes(b"offline-qa-only")
    checksum(backup)


def write_new(folder: Path) -> None:
    """Write a new private QA receipt, not a signed deployment approval."""
    version = folder / "RELEASE_VERSION"
    receipt = folder / "new-deployed.json"
    version.write_text(NEW + "\n")
    version.chmod(0o600)
    receipt.write_text(
        json.dumps(
            {
                "version": NEW,
                "digest": "sha256:" + "b" * 64,
                "source_commit": "b" * 40,
            }
        )
    )
    receipt.chmod(0o600)


def main() -> int:
    """Refuse unsafe paths, existing fixture trees and nonroot execution."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    if os.geteuid() != 0 or args.directory != ALLOWED:
        parser.error("disposable LXC114 root QA path is required")
    if ALLOWED.exists() or ALLOWED.is_symlink():
        parser.error("QA fixture already exists")
    ALLOWED.mkdir(mode=0o700)
    write_source(ALLOWED / "upgrades")
    write_signed(ALLOWED / f"20261118T015250Z-{NEW}")
    write_new(ALLOWED)
    print("QA_SYNTHETIC_SOURCE_OCI_PAIR_READY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
