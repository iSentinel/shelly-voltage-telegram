"""Test private read-only GitHub release messages in three languages."""

import json
import unittest
from unittest.mock import patch

import update_info


def release(version="0.5.13-hw"):
    """Return published release metadata with verified source assets."""

    return {
        "tag_name": "v" + version,
        "assets": [
            {"name": f"shelly-voltage-telegram-{version}.tar.gz"},
            {"name": "SHA256SUMS"},
        ],
    }


class GitHubUpdateInfoTests(unittest.TestCase):
    """Cover localized messages without sending Telegram or HTTP requests."""

    def test_available_release_is_reported(self):
        """Show an available stable update but never advertise auto-install."""

        with patch("urllib.request.urlopen", return_value=_Reply(release())):
            result = update_info.release_notice("0.5.12-hw")
        self.assertIn("Update available: yes", result)
        self.assertIn("Automatic installation: disabled", result)
        self.assertIn(
            "https://github.com/iSentinel/"
            "shelly-voltage-telegram/releases/tag/v0.5.13-hw",
            result,
        )

    def test_current_release_has_no_update(self):
        """Treat a matching release as already current."""

        with patch(
            "urllib.request.urlopen",
            return_value=_Reply(release("0.5.12-hw")),
        ):
            result = update_info.release_notice("0.5.12-hw")
        self.assertIn("Update available: no", result)

    def test_russian_ukrainian_and_english_labels(self):
        """Honor the administrator's configured Telegram language."""

        for language, label in (
            ("ru", "Обновление доступно: да"),
            ("uk", "Оновлення доступне: так"),
            ("en", "Update available: yes"),
        ):
            with (
                self.subTest(language=language),
                patch(
                    "urllib.request.urlopen", return_value=_Reply(release())
                ),
            ):
                result = update_info.release_notice("0.5.12-hw", language)
                self.assertIn(label, result)

    def test_refuse_prerelease_and_unsupported_tag(self):
        """Ignore draft, prerelease and malformed version metadata."""

        for metadata in (
            {**release(), "prerelease": True},
            {**release(), "draft": True},
            {**release(), "tag_name": "latest"},
        ):
            with (
                patch("urllib.request.urlopen", return_value=_Reply(metadata)),
                self.assertRaises(ValueError),
            ):
                update_info.release_notice("0.5.12-hw")

    def test_malformed_api_data_is_rejected_safely(self):
        """Unexpected API shapes must not crash the Telegram command."""

        for response in (
            None,
            [release()],
            {**release(), "assets": None},
            {**release(), "assets": "unexpected"},
            {**release(), "tag_name": "0.5.13-hw"},
        ):
            with (
                self.subTest(response=type(response).__name__),
                patch(
                    "urllib.request.urlopen",
                    return_value=_Reply(response),
                ),
                self.assertRaises(ValueError),
            ):
                update_info.release_notice("0.5.12-hw")

    def test_invalid_json_is_rejected(self):
        """Do not accept incomplete GitHub JSON as a stable release."""

        response = _Reply(release())
        response.payload = b'{"tag_name":'
        with (
            patch("urllib.request.urlopen", return_value=response),
            self.assertRaises(ValueError),
        ):
            update_info.release_notice("0.5.12-hw")

    def test_unbounded_github_response_is_rejected(self):
        """Never read arbitrarily large release JSON into the bot."""

        oversized = {**release(), "body": "a" * 150000}
        with (
            patch(
                "urllib.request.urlopen",
                return_value=_Reply(oversized),
            ),
            self.assertRaisesRegex(ValueError, "too large"),
        ):
            update_info.release_notice("0.5.12-hw")

    def test_invalid_asset_names_do_not_crash_the_command(self):
        """Reject missing filenames even when GitHub sends nested values."""

        expected = "shelly-voltage-telegram-0.5.13-hw.tar.gz"
        malformed = {"name": ["SHA256SUMS"]}
        with (
            patch(
                "urllib.request.urlopen",
                return_value=_Reply(
                    {**release(), "assets": [{"name": expected}, malformed]}
                ),
            ),
            self.assertRaisesRegex(ValueError, "source archive"),
        ):
            update_info.release_notice("0.5.12-hw")

    def test_irrelevant_invalid_asset_name_is_ignored(self):
        """An unrelated invalid asset does not hide valid release files."""

        assets = [*release()["assets"], {"name": {"invalid": "name"}}]
        with patch(
            "urllib.request.urlopen",
            return_value=_Reply({**release(), "assets": assets}),
        ):
            result = update_info.release_notice("0.5.12-hw")
        self.assertIn("Update available: yes", result)

    def test_incomplete_release_cannot_be_advertised(self):
        """Require a source archive and checksum before offering an update."""

        for assets in (
            [],
            [{"name": "SHA256SUMS"}],
            [{"name": "shelly-voltage-telegram-0.5.13-hw.tar.gz"}],
        ):
            with (
                patch(
                    "urllib.request.urlopen",
                    return_value=_Reply({**release(), "assets": assets}),
                ),
                self.assertRaisesRegex(ValueError, "source archive"),
            ):
                update_info.release_notice("0.5.12-hw")


class _Reply:
    """Emulate a small HTTPS JSON response."""

    def __init__(self, data):
        """Encode the GitHub API test metadata."""

        self.payload = json.dumps(data).encode()

    def __enter__(self):
        """Enter the fake network response."""

        return self

    def __exit__(self, *_arguments):
        """Leave the response without hiding errors."""

        return False

    def read(self, size=-1):
        """Return only the requested response bytes."""

        if size < 0:
            return self.payload
        return self.payload[:size]


if __name__ == "__main__":
    unittest.main()
