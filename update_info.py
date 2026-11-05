"""Read-only GitHub release information for Telegram administrators."""

from __future__ import annotations

import re
import urllib.error
import urllib.request
import json

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


def release_notice(installed: str) -> str:
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
    return (
        f"Installed: {installed}\n"
        f"Latest stable GitHub release: {newest}\n"
        f"Update available: {'yes' if available else 'no'}\n"
        "Automatic installation: disabled\n"
        "Installation requires explicit administrator approval on the host."
    )
