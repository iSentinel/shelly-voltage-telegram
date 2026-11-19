#!/usr/bin/env python3
"""Check public source release asset completeness without trusting it."""

from __future__ import annotations

import argparse
import json
import re
import stat
import sys
from pathlib import Path
from urllib.parse import urlparse

VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+-hw\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


class InventoryBlocked(ValueError):
    """A public release is not ready for cryptographic verification."""


def arguments() -> argparse.Namespace:
    """Accept one saved public release response and immutable identifiers."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-json", type=Path, required=True)
    parser.add_argument("--tag-ref-json", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--digest", required=True)
    parser.add_argument("--jsonl", action="store_true")
    return parser.parse_args()


def load_public_release(path: Path) -> dict:
    """Read a size-limited ordinary file without following links."""
    if not path.is_absolute() or ".." in path.parts:
        raise InventoryBlocked("INVENTORY_RELEASE_PATH_INVALID")
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise InventoryBlocked("INVENTORY_RELEASE_FILE_UNSAFE")
    if not 0 < info.st_size <= 1024 * 1024:
        raise InventoryBlocked("INVENTORY_RELEASE_FILE_SIZE_INVALID")
    with path.open("rb") as stream:
        result = json.load(stream)
    if not isinstance(result, dict):
        raise InventoryBlocked("INVENTORY_RELEASE_JSON_INVALID")
    return result


def validate_identity(args: argparse.Namespace, data: dict) -> None:
    """Reject wrong versions, mutable Git refs and incomplete OCI IDs."""
    if not VERSION.fullmatch(args.version):
        raise InventoryBlocked("INVENTORY_VERSION_INVALID")
    if not COMMIT.fullmatch(args.commit):
        raise InventoryBlocked("INVENTORY_COMMIT_INVALID")
    if not DIGEST.fullmatch(args.digest):
        raise InventoryBlocked("INVENTORY_OCI_DIGEST_INVALID")
    if data.get("tag_name") != "v" + args.version:
        raise InventoryBlocked("INVENTORY_RELEASE_TAG_MISMATCH")
    if data.get("draft") is not False or data.get("prerelease") is not False:
        raise InventoryBlocked("INVENTORY_RELEASE_UNPUBLISHED")
    page = data.get("html_url")
    expected = (
        "https://github.com/iSentinel/shelly-voltage-telegram/releases/tag/v"
    )
    if page != expected + args.version:
        raise InventoryBlocked("INVENTORY_RELEASE_PAGE_MISMATCH")


def verify_tag_ref(args: argparse.Namespace) -> None:
    """Bind public Git tag metadata to one exact immutable source SHA."""
    data = load_public_release(args.tag_ref_json)
    if data.get("ref") != "refs/tags/v" + args.version:
        raise InventoryBlocked("INVENTORY_TAG_REF_MISMATCH")
    obj = data.get("object")
    if not isinstance(obj, dict):
        raise InventoryBlocked("INVENTORY_TAG_OBJECT_INVALID")
    if obj.get("type") != "commit":
        raise InventoryBlocked("INVENTORY_TAG_PEELED_COMMIT_REQUIRED")
    if obj.get("sha") != args.commit:
        raise InventoryBlocked("INVENTORY_TAG_COMMIT_MISMATCH")


def expected_assets(version: str) -> set[str]:
    """Name all three files needed for a future signed source proof."""
    archive = f"shelly-voltage-telegram-{version}.tar.gz"
    return {archive, archive + ".sigstore.json", "SHA256SUMS"}


def release_assets(data: dict, version: str) -> dict[str, dict]:
    """Reject duplicate names and URLs outside this release's assets."""
    assets = data.get("assets")
    if not isinstance(assets, list) or len(assets) > 100:
        raise InventoryBlocked("INVENTORY_ASSET_LIST_INVALID")
    result = {}
    for item in assets:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise InventoryBlocked("INVENTORY_ASSET_INVALID")
        name = item["name"]
        if name in result:
            raise InventoryBlocked("INVENTORY_ASSET_DUPLICATED")
        url = item.get("browser_download_url", "")
        parts = urlparse(url) if isinstance(url, str) else None
        if parts is None or parts.scheme != "https":
            raise InventoryBlocked("INVENTORY_ASSET_URL_INVALID")
        expected_url = (
            "https://github.com/iSentinel/shelly-voltage-telegram/"
            f"releases/download/v{version}/{name}"
        )
        if (
            parts.hostname != "github.com"
            or parts.username
            or parts.password
            or url != expected_url
        ):
            raise InventoryBlocked("INVENTORY_ASSET_URL_INVALID")
        if item.get("state") != "uploaded" or item.get("size", 0) <= 0:
            raise InventoryBlocked("INVENTORY_ASSET_NOT_UPLOADED")
        result[name] = item
    return result


def check(args: argparse.Namespace) -> dict:
    """Check public inventory only; never assert a signature was verified."""
    data = load_public_release(args.release_json)
    validate_identity(args, data)
    verify_tag_ref(args)
    assets = release_assets(data, args.version)
    missing = sorted(expected_assets(args.version) - assets.keys())
    if missing:
        raise InventoryBlocked(
            "INVENTORY_REQUIRED_ASSETS_MISSING:" + ",".join(missing)
        )
    return {
        "status": "PASS_PUBLIC_INVENTORY_ONLY",
        "version": args.version,
        "commit_format_valid": True,
        "tag_commit_matches_input": True,
        "tag_metadata_cryptographically_verified": False,
        "digest_format_valid": True,
        "source_signature_verified": False,
        "oci_signature_verified": False,
        "release_authorized": False,
        "rollback_authorized": False,
        "production_modified": False,
    }


def main() -> int:
    """Report a non-authorizing asset inventory with stable exit codes."""
    args = arguments()
    try:
        report = check(args)
    except (ValueError, OSError, TypeError, json.JSONDecodeError) as exc:
        report = {
            "status": "BLOCKED",
            "reason": str(exc)
            if isinstance(exc, InventoryBlocked)
            else "INVENTORY_UNAVAILABLE",
            "release_authorized": False,
            "rollback_authorized": False,
            "production_modified": False,
        }
    if args.jsonl:
        print(json.dumps(report, separators=(",", ":")))
    else:
        print("PUBLIC RELEASE INVENTORY:", report["status"])
        if report["status"] == "BLOCKED":
            print("reason:", report["reason"])
    return 0 if report["status"] == "PASS_PUBLIC_INVENTORY_ONLY" else 3


if __name__ == "__main__":
    sys.exit(main())
