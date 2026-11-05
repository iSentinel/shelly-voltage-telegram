"""Test read-only GitHub update information for Telegram."""

import json
import unittest
from unittest.mock import patch

import update_info


class GitHubUpdateInfoTests(unittest.TestCase):
    """Cover release notices without requiring GitHub connectivity."""

    def test_available_release_is_reported(self):
        """Show the newer stable version without installing it."""

        response = _Reply({"tag_name": "v0.5.13-hw"})
        with patch("urllib.request.urlopen", return_value=response):
            result = update_info.release_notice("0.5.12-hw")
        self.assertIn("Update available: yes", result)
        self.assertIn("Automatic installation: disabled", result)

    def test_current_release_has_no_update(self):
        """Treat a matching release as current."""

        with patch(
            "urllib.request.urlopen",
            return_value=_Reply({"tag_name": "v0.5.12-hw"}),
        ):
            result = update_info.release_notice("0.5.12-hw")
        self.assertIn("Update available: no", result)

    def test_refuse_prerelease_and_unsupported_tag(self):
        """Do not advertise invalid or prerelease tags."""

        for release in (
            {"tag_name": "v0.5.13-hw", "prerelease": True},
            {"tag_name": "latest"},
        ):
            with (
                patch(
                    "urllib.request.urlopen",
                    return_value=_Reply(release),
                ),
                self.assertRaises(ValueError),
            ):
                update_info.release_notice("0.5.12-hw")


class _Reply:
    """Emulate a JSON-only HTTP response."""

    def __init__(self, data):
        """Record a small test response."""

        self.payload = json.dumps(data).encode()

    def __enter__(self):
        """Support a context-managed HTTP request."""

        return self

    def __exit__(self, *_arguments):
        """Close the fake connection without hiding errors."""

        return False

    def read(self):
        """Return encoded test content."""

        return self.payload


if __name__ == "__main__":
    unittest.main()
