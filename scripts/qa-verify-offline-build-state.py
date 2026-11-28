#!/usr/bin/env python3
"""Check exact QA build inputs and installed package metadata."""

import argparse
import hashlib
import importlib.metadata
import json
import platform
import re
import subprocess
from pathlib import Path


def digest(path):
    """Hash a file without importing or executing its contents."""
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify_before(root, expected, status):
    """Reject an unexpected base or changed staged payload."""
    if digest(status) != expected["base_status_sha256"]:
        raise ValueError("Base dpkg status mismatch")
    if platform.machine() != expected["machine"]:
        raise ValueError("Base machine mismatch")
    if platform.python_version() != "3.13.18":
        raise ValueError("Base Python version mismatch")
    manifest = root / "input-manifest.json"
    if digest(manifest) != expected["input_manifest_sha256"]:
        raise ValueError("Input manifest mismatch")
    files = json.loads(manifest.read_bytes())["files"]
    actual = {
        str(path.relative_to(root))
        for folder in ("runtime", "debs", "wheels")
        for path in (root / folder).iterdir()
    } | {"requirements.lock"}
    if actual != set(files):
        raise ValueError("Staged file set mismatch")
    for name, sha in files.items():
        if digest(root / name) != sha:
            raise ValueError(f"Staged SHA256 mismatch: {name}")


def installed_debian():
    """Return the installed Debian name/version/architecture mapping."""
    data = subprocess.check_output(
        [
            "dpkg-query",
            "-W",
            "-f=${Package}|${Version}|${Architecture}|${Status}\\n",
        ],
        text=True,
    )
    result = {}
    for line in data.splitlines():
        name, version, arch, status = line.split("|")
        if status == "install ok installed":
            if name in result:
                raise ValueError("Duplicate installed Debian package")
            result[name] = [version, arch]
    return result


def installed_python():
    """Return normalized names and versions from installed metadata."""
    result = {}
    for dist in importlib.metadata.distributions():
        name = re.sub(r"[-_.]+", "-", dist.metadata["Name"]).lower()
        if name in result:
            raise ValueError("Duplicate installed Python distribution")
        result[name] = dist.version
    return result


def verify_after(expected):
    """Require exact installed inventories; not a native runtime test."""
    if installed_debian() != expected["debian"]:
        raise ValueError("Final Debian inventory mismatch")
    if installed_python() != expected["python"]:
        raise ValueError("Final Python inventory mismatch")


def main():
    """Run a before or after check within a future QA build."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("before", "after"))
    parser.add_argument("--root", type=Path, default=Path("/qa"))
    args = parser.parse_args()
    expected = json.loads((args.root / "expected-state.json").read_bytes())
    try:
        if args.phase == "before":
            verify_before(args.root, expected, Path("/var/lib/dpkg/status"))
        else:
            verify_after(expected)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"QA BUILD STATE: FAIL: {exc}\n")
    print(f"QA BUILD STATE: PASS {args.phase}")


if __name__ == "__main__":
    main()
