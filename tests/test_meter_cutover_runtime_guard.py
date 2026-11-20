"""Synthetic fail-closed checks for a saved Telegram meter setting."""

from __future__ import annotations

import hashlib
import importlib.util
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
    "scripts/meter-cutover-runtime-guard.py"
)
SPEC = importlib.util.spec_from_file_location("meter_cutover_guard", SCRIPT)
assert SPEC and SPEC.loader
GUARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GUARD)


class MeterCutoverGuardTests(unittest.TestCase):
    """Never mutate generated SQLite or a secret-bearing env file."""

    def setUp(self) -> None:
        """Set up a private synthetic EM-50-to-Pro3EM switch."""

        temp = tempfile.TemporaryDirectory(prefix="svt-meter-cutover-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.env = self.root / ".env"
        self.env.write_text(
            "TELEGRAM_BOT_TOKEN=synthetic-private-secret\n"
            "SHELLY_HOST=192.0.2.50\n"
            "SHELLY_METER_TYPE=pro3em\n",
            encoding="utf-8",
        )
        self.env.chmod(0o600)
        self.db = self.root / "voltage.sqlite3"
        with closing(sqlite3.connect(self.db)) as conn:
            conn.executescript(
                "CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);"
                "CREATE TABLE measurements(id INTEGER PRIMARY KEY,"
                " voltage REAL);"
                "INSERT INTO measurements VALUES (1, 210.5);"
                "INSERT INTO meta VALUES "
                "('telegram_setting_meter_type', 'pro_em50');"
            )
            conn.commit()
        self.db.chmod(0o600)

    def test_stale_saved_em50_blocks_new_three_phase_env(self):
        """Reproduce actual failure before Docker is ever restarted."""

        result = GUARD.assess(self.env, self.db, "pro3em")
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["reason"], "SAVED_METER_TYPE_OVERRIDES_ENV")
        self.assertEqual(result["saved_meter_type"], "pro_em50")
        self.assertFalse(result["release_authorized"])

    def test_matching_saved_and_env_model_passes_static_only(self):
        """A matching preference is evidence, not release authorization."""

        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute(
                "UPDATE meta SET value='pro3em' "
                "WHERE key='telegram_setting_meter_type'"
            )
            conn.commit()
        result = GUARD.assess(self.env, self.db, "pro3em")
        self.assertEqual(result["status"], "PASS_STATIC_METER_PARITY")
        self.assertTrue(result["no_writes"])
        self.assertFalse(result["service_modified"])
        self.assertFalse(result["release_authorized"])

    def test_missing_saved_setting_uses_env_fallback(self):
        """Absent Telegram preference does not override the environment."""

        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute("DELETE FROM meta")
            conn.commit()
        result = GUARD.assess(self.env, self.db, "pro3em")
        self.assertEqual(result["status"], "PASS_STATIC_METER_PARITY")
        self.assertIsNone(result["saved_meter_type"])

    def test_env_mismatch_blocks_even_matching_saved_preference(self):
        """The intended model must match env and stored selection."""

        result = GUARD.assess(self.env, self.db, "pro_em50")
        self.assertEqual(result["reason"], "ENV_METER_TYPE_MISMATCH")

    def test_unknown_saved_model_is_denied_without_echo(self):
        """Do not leak untrusted metadata strings through a JSON report."""

        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute(
                "UPDATE meta SET value='TOKEN_PRIVATE_SHOULD_NOT_PRINT'"
            )
            conn.commit()
        result = GUARD.assess(self.env, self.db, "pro3em")
        self.assertEqual(result["reason"], "METER_TYPE_UNKNOWN")
        self.assertNotIn("TOKEN_PRIVATE", json.dumps(result))

    def test_unknown_expected_model_fails_closed(self):
        """Reject unsupported targets without even reading secrets."""

        result = GUARD.assess(self.env, self.db, "not_a_model")
        self.assertEqual(result["reason"], "EXPECTED_METER_TYPE_UNKNOWN")
        self.assertNotIn("not_a_model", json.dumps(result))

    def test_saved_model_must_match_runtime_spelling_exactly(self):
        """The preflight must reject values the runtime cannot use."""

        for value in ("PRO3EM", " pro3em ", "'pro3em'"):
            with self.subTest(value=value):
                with closing(sqlite3.connect(self.db)) as conn, conn:
                    conn.execute("UPDATE meta SET value=?", (value,))
                result = GUARD.assess(self.env, self.db, "pro3em")
                self.assertEqual(result["status"], "BLOCKED")
                self.assertEqual(result["reason"], "METER_TYPE_UNKNOWN")

    def test_duplicate_saved_model_is_rejected(self):
        """Do not trust the first row of corrupted settings data."""

        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.executescript(
                "DROP TABLE meta;"
                "CREATE TABLE meta(key TEXT, value TEXT);"
                "INSERT INTO meta VALUES "
                "('telegram_setting_meter_type', 'pro3em'), "
                "('telegram_setting_meter_type', 'pro_em50');"
            )
        result = GUARD.assess(self.env, self.db, "pro3em")
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["reason"], "SAVED_METER_TYPE_NOT_UNIQUE")

    def test_unknown_env_type_fails_closed(self):
        """A mistyped env model is not a safe target."""

        self.env.write_text("SHELLY_METER_TYPE=bad-model\n")
        self.assertEqual(
            GUARD.assess(self.env, self.db, "pro3em")["reason"],
            "METER_TYPE_UNKNOWN",
        )

    def test_duplicate_env_choice_fails_closed(self):
        """Repeated model assignments cannot be silently accepted."""

        with self.env.open("a") as stream:
            stream.write("SHELLY_METER_TYPE=pro_em50\n")
        self.assertEqual(
            GUARD.assess(self.env, self.db, "pro3em")["reason"],
            "ENV_METER_TYPE_NOT_UNIQUE",
        )

    def test_missing_env_choice_fails_closed(self):
        """Never infer model from old SQLite when env is ambiguous."""

        self.env.write_text("SHELLY_HOST=192.0.2.50\n")
        self.assertEqual(
            GUARD.assess(self.env, self.db, "pro3em")["reason"],
            "ENV_METER_TYPE_NOT_UNIQUE",
        )

    def test_quoted_env_value_is_supported(self):
        """Regular quoted .env model names are accepted safely."""

        self.env.write_text("SHELLY_METER_TYPE='pro_em50'\n")
        self.assertEqual(
            GUARD.assess(self.env, self.db, "pro_em50")["status"],
            "PASS_STATIC_METER_PARITY",
        )

    def test_missing_sqlite_is_blocked(self):
        """A missing persisted state file cannot be guessed."""

        self.db.unlink()
        self.assertEqual(
            GUARD.assess(self.env, self.db, "pro3em")["reason"],
            "INPUT_MISSING",
        )

    def test_invalid_sqlite_is_blocked(self):
        """Bad SQLite content cannot be mistaken for absent override."""

        self.db.write_text("not a sqlite database")
        self.assertEqual(
            GUARD.assess(self.env, self.db, "pro3em")["reason"],
            "SAVED_SETTING_DB_UNREADABLE",
        )

    def test_linked_env_path_is_blocked(self):
        """Do not read a symlink substituted for a secret-bearing env."""

        link = self.root / "linked.env"
        link.symlink_to(self.env)
        self.assertEqual(
            GUARD.assess(link, self.db, "pro3em")["reason"],
            "INPUT_PATH_UNSAFE",
        )

    def test_linked_sqlite_path_is_blocked(self):
        """Reject a linked history database before any SQLite read."""

        link = self.root / "linked.sqlite3"
        link.symlink_to(self.db)
        self.assertEqual(
            GUARD.assess(self.env, link, "pro3em")["reason"],
            "INPUT_PATH_UNSAFE",
        )

    def test_hardlinked_database_is_blocked(self):
        """Do not trust ambiguous SQLite source identities."""

        os.link(self.db, self.root / "duplicate.sqlite3")
        self.assertEqual(
            GUARD.assess(self.env, self.db, "pro3em")["reason"],
            "INPUT_NOT_REGULAR",
        )

    def test_cli_does_not_modify_database_or_env(self):
        """All preflight code paths leave every fixture byte unchanged."""

        before = {
            file: (
                hashlib.sha256(file.read_bytes()).hexdigest(),
                file.stat().st_mtime_ns,
            )
            for file in (self.env, self.db)
        }
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--env-file",
                str(self.env),
                "--db",
                str(self.db),
                "--expected-meter",
                "pro3em",
                "--jsonl",
            ],
            text=True,
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 2)
        output = json.loads(result.stdout)
        self.assertEqual(output["reason"], "SAVED_METER_TYPE_OVERRIDES_ENV")
        self.assertNotIn("synthetic-private-secret", result.stdout)
        self.assertEqual(
            before,
            {
                file: (
                    hashlib.sha256(file.read_bytes()).hexdigest(),
                    file.stat().st_mtime_ns,
                )
                for file in (self.env, self.db)
            },
        )


if __name__ == "__main__":
    unittest.main()
