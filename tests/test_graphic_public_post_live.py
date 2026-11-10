"""Check live public-post audit decisions without calling Telegram or t.me."""

from __future__ import annotations

import importlib.util
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / (
    "scripts/graphic-public-post-audit.py"
)
CHAT = "-100123"
NAME = "sample_status"
PAGE = (
    '<div class="tgme_widget_message" data-post="sample_status/101">'
    '<img class="tgme_widget_message_user_photo">'
    '<div class="tgme_widget_message_text">Status</div></div>'
)


class PublicPostLiveTests(unittest.TestCase):
    """Test getChat channel binding and post isolation in memory."""

    def setUp(self):
        """Create disposable private SQLite metadata."""

        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.database = Path(temp.name) / "voltage.sqlite3"
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)")
            db.execute(
                "INSERT INTO meta VALUES (?,?)",
                ("telegram_status_message_id", "101"),
            )
        spec = importlib.util.spec_from_file_location(
            "post_audit_test", SCRIPT
        )
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.args = SimpleNamespace(
            database=self.database,
            chat_env="TEST_CHAT",
            token_file=Path(temp.name) / "dummy-token",
            html_file=None,
            public_username=None,
        )

    def getchat(self, **overrides):
        """Build mock getChat response with channel identity."""

        details = {"type": "channel", "id": int(CHAT), "username": NAME}
        details.update(overrides)
        return {"ok": True, "result": details}

    def check_live(self, response, page=PAGE):
        """Run only mocked getChat and public page fetch."""

        shared = self.module.shared_reader()
        with (
            mock.patch.dict(os.environ, {"TEST_CHAT": CHAT}),
            mock.patch.object(shared, "request_chat", return_value=response),
            mock.patch.object(
                self.module, "shared_reader", return_value=shared
            ),
            mock.patch.object(
                self.module, "fetch_public_post", return_value=page
            ) as page_reader,
        ):
            result = self.module.audit(self.args)
        page_reader.assert_called_once_with(NAME, "101")
        return result

    def test_exact_live_channel_and_post_are_review_only(self):
        """Matching HTML is not sufficient for a public migration."""

        result = self.check_live(self.getchat())
        self.assertEqual(result["status"], "PUBLIC_POST_MATCH_FOR_REVIEW")
        self.assertEqual(result["media_kind"], "text")
        self.assertTrue(result["live_page_checked"])
        self.assertFalse(result["deployment_allowed"])
        self.assertFalse(result["sqlite_modified"])
        self.assertFalse(result["telegram_posts_or_edits"])

    def test_other_api_channel_is_blocked(self):
        """A public page from a different channel is not evidence."""

        with self.assertRaisesRegex(ValueError, "CHANNEL_MISMATCH"):
            self.check_live(self.getchat(id=-100999))

    def test_missing_public_username_is_blocked(self):
        """Private and anonymous channels cannot use web-preview evidence."""

        with self.assertRaisesRegex(ValueError, "PUBLIC_USERNAME_UNAVAILABLE"):
            self.check_live(self.getchat(username=None))

    def test_manual_username_override_is_refused(self):
        """Live mode must derive username from verified Bot API getChat."""

        self.args.public_username = NAME
        with (
            mock.patch.dict(os.environ, {"TEST_CHAT": CHAT}),
            self.assertRaisesRegex(
                ValueError, "LIVE_USERNAME_OVERRIDE_REFUSED"
            ),
        ):
            self.module.audit(self.args)

    def test_sqlite_changed_during_public_fetch_is_blocked(self):
        """Never use an earlier message ID after a concurrent status edit."""

        shared = self.module.shared_reader()

        def fake_fetch(_username, _message_id):
            with closing(sqlite3.connect(self.database)) as db, db:
                db.execute(
                    "UPDATE meta SET value='102' "
                    "WHERE key='telegram_status_message_id'"
                )
            return PAGE

        with (
            mock.patch.dict(os.environ, {"TEST_CHAT": CHAT}),
            mock.patch.object(
                shared, "request_chat", return_value=self.getchat()
            ),
            mock.patch.object(
                self.module, "shared_reader", return_value=shared
            ),
            mock.patch.object(self.module, "fetch_public_post", fake_fetch),
            self.assertRaisesRegex(ValueError, "STATUS_CHANGED_DURING_AUDIT"),
        ):
            self.module.audit(self.args)


if __name__ == "__main__":
    unittest.main()
