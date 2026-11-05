"""Read-only GitHub release information for Telegram administrators."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from telegram_i18n import tr

RELEASE_URL = (
    "https://api.github.com/repos/iSentinel/"
    "shelly-voltage-telegram/releases/latest"
)
VERSION_PATTERN = re.compile(r"v?(\d+)\.(\d+)\.(\d+)-hw\Z")


def version_numbers(version: str) -> tuple[int, int, int]:
    """Parse project version numbers for comparisons."""

    match = VERSION_PATTERN.fullmatch(version)
    if not match:
        raise ValueError("unsupported version returned by GitHub")
    return tuple(int(value) for value in match.groups())


def published_release_tag() -> str:
    """Read a bounded and complete stable GitHub release."""

    headers = {"User-Agent": "shelly-voltage-telegram/updates"}
    request = urllib.request.Request(RELEASE_URL, headers=headers)
    with urllib.request.urlopen(request, timeout=12) as response:
        raw = response.read(131073)
    if len(raw) > 131072:
        raise ValueError("GitHub release metadata is too large")
    release = json.loads(raw)
    if not isinstance(release, dict):
        raise ValueError("invalid GitHub release metadata")
    if release.get("draft") or release.get("prerelease"):
        raise ValueError("no stable GitHub release was returned")
    tag = str(release.get("tag_name", ""))
    if not tag.startswith("v"):
        raise ValueError("release tag has no stable version prefix")
    newest = tag.removeprefix("v")
    version_numbers(newest)
    expected = {f"shelly-voltage-telegram-{newest}.tar.gz", "SHA256SUMS"}
    source_assets = release.get("assets")
    if not isinstance(source_assets, list):
        raise ValueError("release asset list is unavailable")
    assets = {
        asset["name"]
        for asset in source_assets
        if isinstance(asset, dict) and isinstance(asset.get("name"), str)
    }
    if not expected.issubset(assets):
        raise ValueError("the release has no verified source archive")
    return tag


def release_notice(installed: str, language: str = "en") -> str:
    """Describe the latest stable release without installing anything."""

    tag = published_release_tag()
    newest = tag.removeprefix("v")
    available = version_numbers(newest) > version_numbers(installed)
    answer = tr(language, "updates_yes" if available else "updates_no")
    lines = [
        tr(language, "updates_installed", version=installed),
        tr(language, "updates_latest", version=newest),
        tr(language, "updates_available", answer=answer),
        tr(language, "updates_auto_disabled"),
        tr(language, "updates_manual_required"),
        "https://github.com/iSentinel/"
        f"shelly-voltage-telegram/releases/tag/{tag}",
    ]
    return "\n".join(lines)
