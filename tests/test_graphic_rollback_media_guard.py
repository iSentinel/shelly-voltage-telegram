"""Reject old-source rollback after public photo conversion."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/graphic-rollback-media-guard.py"
SPEC = importlib.util.spec_from_file_location("rollback_media_guard", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
GUARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GUARD)


class GraphicRollbackMediaGuardTests(unittest.TestCase):
    """Never contact Telegram or modify a production SQLite database."""

    def setUp(self) -> None:
        """Create a disposable database with a legacy status message."""

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.db = self.folder / "voltage.sqlite3"
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("create table meta(key text primary key,value text)")
            conn.execute(
                "insert into meta values (?,?)",
                ("telegram_status_message_id", "101"),
            )

    def set_meta(self, key: str, value: str) -> None:
        """Simulate one protected Telegram metadata transition."""

        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute(
                "insert or replace into meta(key,value) values (?,?)",
                (key, value),
            )

    def call(self) -> tuple[int, dict]:
        """Exercise the real CLI with structured, redacted output."""

        proc = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--database",
                str(self.db),
                "--jsonl",
            ],
            capture_output=True,
            text=True,
            timeout=6,
            check=False,
        )
        return proc.returncode, json.loads(proc.stdout)

    def test_unbound_database_and_missing_database_are_safe(self) -> None:
        """Unmigrated text-only deployments still support rollback."""

        code, report = self.call()
        self.assertEqual(code, 0)
        self.assertEqual(report["reason"], "legacy_unbound")
        self.assertEqual(
            GUARD.inspect(self.folder / "missing.sqlite3")["status"], "PASS"
        )

    def test_verified_text_identity_remains_rollback_compatible(self) -> None:
        """A verified text status still supports editMessageText."""

        self.set_meta("telegram_status_media_identity", "text:-100123:101")
        code, report = self.call()
        self.assertEqual(code, 0)
        self.assertEqual(report["reason"], "confirmed_text")

    def test_photo_or_unknown_media_blocks_rollback(self) -> None:
        """Old source cannot edit an already-converted Telegram photo."""

        for kind in ("photo", "unknown", "video", ""):
            with self.subTest(kind=kind):
                self.set_meta(
                    "telegram_status_media_identity", f"{kind}:-100123:101"
                )
                code, report = self.call()
                self.assertEqual(code, 6)
                self.assertEqual(report["status"], "BLOCKED")
                self.assertNotIn("-100123", json.dumps(report))

    def test_pending_keys_block_without_a_media_marker(self) -> None:
        """Unknown Telegram results cannot be erased by a DB restore."""

        for key in GUARD.PENDING:
            with self.subTest(key=key):
                self.set_meta(key, "pending")
                code, report = self.call()
                self.assertEqual(code, 6)
                self.assertEqual(report["status"], "BLOCKED")
                with closing(sqlite3.connect(self.db)) as conn, conn:
                    conn.execute("delete from meta where key=?", (key,))

    def test_mismatched_or_malformed_text_marker_is_unsafe(self) -> None:
        """A forged text marker cannot bypass the rollback guard."""

        for marker in (
            "text:-100123:102",
            "text::101",
            "text:abc",
            "text:-100123:0",
        ):
            with self.subTest(marker=marker):
                self.set_meta("telegram_status_media_identity", marker)
                code, _ = self.call()
                self.assertEqual(code, 6)

    def test_corrupt_database_and_symlink_fail_closed(self) -> None:
        """Do not follow linked databases or accept missing metadata."""

        linked = self.folder / "alias.sqlite3"
        linked.symlink_to(self.db)
        with self.assertRaises(ValueError):
            GUARD.inspect(linked)
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("drop table meta")
        self.assertEqual(self.call()[0], 6)

    def test_guard_runs_before_service_stop_and_db_replacement(self) -> None:
        """Shell rollback must check live media before mutation."""

        source = (ROOT / "scripts/rollback.sh").read_text()
        main = source.split("main() {", 1)[1].split('\nmain "$@"', 1)[0]
        self.assertIn("graphic-rollback-media-guard.py", main)
        self.assertLess(
            main.index("graphic-rollback-media-guard.py"),
            main.index("stop_service\n"),
        )
        self.assertLess(
            main.index("graphic-rollback-media-guard.py"),
            main.index("restore_database\n"),
        )


if __name__ == "__main__":
    unittest.main()
