"""Check exact public Telegram status evidence without mutating a channel."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / (
    "scripts/graphic-public-post-audit.py"
)
USERNAME = "sample_status"
CHAT = "-100123"


def sample_page(
    post: str = f"{USERNAME}/101", text: bool = True, photo: bool = False
) -> str:
    """Build a minimal Telegram HTML post from synthetic values."""

    body = '<div class="tgme_widget_message_user_photo"></div>'
    if text:
        body += '<div class="tgme_widget_message_text">Status</div>'
    if photo:
        body += '<a class="tgme_widget_message_photo_wrap"></a>'
    return (
        '<html><div class="tgme_widget_message" data-post="'
        + post
        + '">'
        + body
        + "</div></html>"
    )


class PublicPostAuditTests(unittest.TestCase):
    """Only exact message evidence may be provisionally classified."""

    def setUp(self):
        """Create disposable local-only SQLite and HTML fixtures."""

        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.database = root / "voltage.sqlite3"
        self.html = root / "post.html"
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)")
            db.execute(
                "INSERT INTO meta VALUES (?,?)",
                ("telegram_status_message_id", "101"),
            )
        self.html.write_text(sample_page())

    def set_meta(self, key: str, value: str):
        """Modify test SQLite without accessing production."""

        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute(
                "INSERT OR REPLACE INTO meta VALUES (?,?)", (key, value)
            )

    def run_check(self):
        """Run only local HTML fixture mode and collect safe JSONL."""

        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--database",
                str(self.database),
                "--html-file",
                str(self.html),
                "--public-username",
                USERNAME,
                "--chat-env",
                "AUDIT_CHAT",
                "--jsonl",
            ],
            env={**os.environ, "AUDIT_CHAT": CHAT},
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )

    def assert_blocked(self, reason: str):
        """Require denial without disclosing private IDs or post text."""

        answer = self.run_check()
        self.assertEqual(answer.returncode, 2, answer.stderr)
        result = json.loads(answer.stdout)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["reason"], reason)
        self.assertFalse(result["deployment_allowed"])
        self.assertFalse(result["sqlite_modified"])
        self.assertFalse(result["telegram_posts_or_edits"])
        self.assertNotIn(CHAT, answer.stdout)
        self.assertNotIn(USERNAME, answer.stdout)
        self.assertNotIn("Traceback", answer.stderr)

    def test_exact_text_fixture_is_only_provisional(self):
        """A saved HTML fixture is not Telegram API verification."""

        answer = self.run_check()
        self.assertEqual(answer.returncode, 0, answer.stderr)
        result = json.loads(answer.stdout)
        self.assertEqual(result["status"], "FIXTURE_MATCH_ONLY")
        self.assertEqual(result["media_kind"], "text")
        self.assertFalse(result["live_page_checked"])
        self.assertFalse(result["deployment_allowed"])

    def test_self_closing_avatar_does_not_hide_text(self):
        """Self-closing avatar imagery must not alter status media type."""

        self.html.write_text(
            '<div class="tgme_widget_message" '
            f'data-post="{USERNAME}/101">'
            '<img class="tgme_widget_message_user_photo"/>'
            '<div class="tgme_widget_message_text">Status</div>'
            "</div>"
        )
        answer = self.run_check()
        self.assertEqual(answer.returncode, 0, answer.stderr)
        self.assertEqual(json.loads(answer.stdout)["media_kind"], "text")

    def test_photo_fixture_is_only_provisional(self):
        """A photo fixture never triggers text edits."""

        self.html.write_text(sample_page(text=False, photo=True))
        answer = self.run_check()
        self.assertEqual(answer.returncode, 0, answer.stderr)
        self.assertEqual(json.loads(answer.stdout)["media_kind"], "photo")

    def test_different_post_is_blocked(self):
        """Do not use a pinned post with a different ID."""

        self.html.write_text(sample_page(post=f"{USERNAME}/102"))
        self.assert_blocked("EXACT_POST_MISMATCH")

    def test_surrounding_text_cannot_affect_media_type(self):
        """Only a message's own children may signal its media format."""

        self.html.write_text(
            '<div class="tgme_widget_message_text">other</div>'
            + sample_page(text=False, photo=True)
        )
        answer = self.run_check()
        self.assertEqual(answer.returncode, 0, answer.stderr)
        self.assertEqual(json.loads(answer.stdout)["media_kind"], "photo")

    def test_duplicate_post_is_blocked(self):
        """Refuse more than one embedded post on a page."""

        self.html.write_text(sample_page() + sample_page())
        self.assert_blocked("EXACT_POST_MISMATCH")

    def test_photo_with_text_caption_is_photo(self):
        """An ordinary photo caption must not trigger an ambiguity error."""

        self.html.write_text(sample_page(text=True, photo=True))
        answer = self.run_check()
        self.assertEqual(answer.returncode, 0, answer.stderr)
        result = json.loads(answer.stdout)
        self.assertEqual(result["media_kind"], "photo")
        self.assertEqual(result["status"], "FIXTURE_MATCH_ONLY")
        self.assertFalse(result["deployment_allowed"])

    def test_photo_caption_with_video_is_ambiguous(self):
        """A photo plus unsupported video markers must fail closed."""

        self.html.write_text(
            sample_page(text=True, photo=True).replace(
                '<a class="tgme_widget_message_photo_wrap"></a>',
                '<a class="tgme_widget_message_photo_wrap"></a>'
                '<div class="tgme_widget_message_video_player"></div>',
            )
        )
        self.assert_blocked("POST_MEDIA_AMBIGUOUS")

    def test_photo_caption_with_document_is_ambiguous(self):
        """Never classify a mixed photo/document as one safe image."""

        self.html.write_text(
            sample_page(text=True, photo=True).replace(
                '<a class="tgme_widget_message_photo_wrap"></a>',
                '<a class="tgme_widget_message_photo_wrap"></a>'
                '<div class="tgme_widget_message_document"></div>',
            )
        )
        self.assert_blocked("POST_MEDIA_AMBIGUOUS")

    def test_photo_caption_conflicting_marker_is_blocked(self):
        """A persisted text identity cannot be overridden by public HTML."""

        self.set_meta("telegram_status_media_identity", f"text:{CHAT}:101")
        self.html.write_text(sample_page(text=True, photo=True))
        self.assert_blocked("MEDIA_MARKER_CONFLICT")

    def test_empty_media_is_ambiguous(self):
        """Author avatars are not real media attachments."""

        self.html.write_text(sample_page(text=False, photo=False))
        self.assert_blocked("POST_MEDIA_AMBIGUOUS")

    def test_empty_pending_record_blocks(self):
        """Any unresolved publication marker refuses verification."""

        self.set_meta("telegram_graphic_publish_pending", "")
        self.assert_blocked("UNRESOLVED_PUBLICATION")

    def test_conflicting_marker_blocks(self):
        """Never override previously stored contradictory type."""

        self.set_meta("telegram_status_media_identity", f"photo:{CHAT}:101")
        self.assert_blocked("MEDIA_MARKER_CONFLICT")

    def test_missing_database_is_not_created(self):
        """An audit must not create a missing SQLite file."""

        self.database.unlink()
        self.assert_blocked("DATABASE_INVALID")
        self.assertFalse(self.database.exists())

    def test_symlinked_html_file_is_rejected(self):
        """Never follow a symlink to a private path."""

        other = self.html.with_name("original.html")
        self.html.rename(other)
        self.html.symlink_to(other)
        self.assert_blocked("INPUT_UNSAFE")

    def test_oversized_html_is_rejected(self):
        """Bound untrusted public HTML bytes."""

        self.html.write_bytes(b"x" * 65537)
        self.assert_blocked("INPUT_UNSAFE")

    def test_help_is_noninteractive(self):
        """Support -h and --help for operators."""

        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("--token-file", result.stdout)
        self.assertIn("--html-file", result.stdout)


if __name__ == "__main__":
    unittest.main()
