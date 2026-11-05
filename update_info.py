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


def release_notice(installed: str, language: str = "en") -> str:
    """Describe the latest stable release without installing anything."""

    headers = {"User-Agent": "shelly-voltage-telegram/updates"}
    request = urllib.request.Request(RELEASE_URL, headers=headers)
    with urllib.request.urlopen(request, timeout=12) as response:
        release = json.load(response)
    if release.get("draft") or release.get("prerelease"):
        raise ValueError("no stable GitHub release was returned")
    tag = str(release.get("tag_name", ""))
    newest = tag.removeprefix("v")
    available = version_numbers(newest) > version_numbers(installed)
    expected = {f"shelly-voltage-telegram-{newest}.tar.gz", "SHA256SUMS"}
    assets = {
        asset.get("name")
        for asset in release.get("assets", [])
        if isinstance(asset, dict)
    }
    if not expected.issubset(assets):
        raise ValueError("the release has no verified source archive")
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
