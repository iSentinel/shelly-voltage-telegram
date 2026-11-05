#!/usr/bin/env python3
"""Validate CI image tags before any image is published or signed."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

RELEASE_TAG = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+-hw\Z")
MANUAL_TAG = re.compile(r"manual-[a-zA-Z0-9][a-zA-Z0-9._-]{0,60}\Z")
BUILD_REF = re.compile(r"[A-Za-z0-9._/@{}^~+-]+\Z")


def version_file(root: Path, name: str) -> str:
    """Read a single installed or release version."""

    value = (root / name).read_text(encoding="utf-8").strip()
    if not RELEASE_TAG.fullmatch("v" + value):
        raise ValueError(f"{name} has an invalid version")
    return value


def tag_commit(root: Path, tag: str) -> str:
    """Find the commit at a local Git tag without executing shell text."""

    return subprocess.check_output(
        ["git", "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}"],
        cwd=root,
        text=True,
    ).strip()


def current_commit(root: Path) -> str:
    """Return the checked-out Git commit ID."""

    return subprocess.check_output(
        ["git", "rev-parse", "--verify", "HEAD"], cwd=root, text=True
    ).strip()


def validate(event: str, ref: str, build_ref: str, image_tag: str, root: Path) -> None:
    """Reserve stable signed versions for exact Git tag push events."""

    if event == "workflow_dispatch":
        if not BUILD_REF.fullmatch(build_ref):
            raise ValueError("manual build ref has invalid characters")
        if image_tag and not MANUAL_TAG.fullmatch(image_tag):
            raise ValueError("manual image tag must use the manual- namespace")
        return
    if event != "push":
        raise ValueError("unsupported workflow event")
    if ref == "refs/heads/main":
        return
    tag = ref.removeprefix("refs/tags/")
    if not ref.startswith("refs/tags/") or not RELEASE_TAG.fullmatch(tag):
        raise ValueError("invalid release tag")
    version = version_file(root, "VERSION")
    if version != version_file(root, "RELEASE_VERSION"):
        raise ValueError("VERSION differs from RELEASE_VERSION")
    if tag != "v" + version:
        raise ValueError("release tag differs from VERSION")
    if tag_commit(root, tag) != current_commit(root):
        raise ValueError("checked-out commit differs from release tag")


def main() -> int:
    """Enforce the policy with a stable pass/fail exit code."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--build-ref", default="")
    parser.add_argument("--image-tag", default="")
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    try:
        validate(
            args.event, args.ref, args.build_ref, args.image_tag, args.root
        )
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("PASS: image release target is authorized")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
