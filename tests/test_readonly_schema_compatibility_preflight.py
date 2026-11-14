"""Protect read-only schema assessment from accidental data changes."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/readonly-schema-compatibility-preflight.py"
)
SPEC = importlib.util.spec_from_file_location("svt_schema_preflight", SCRIPT)
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)


class ReadonlySchemaCompatibilityTests(unittest.TestCase):
    """Create fully synthetic sources and databases in isolated /tmp."""

    def setUp(self) -> None:
        """Build a valid synthetic v3 candidate and schema."""
        directory = tempfile.TemporaryDirectory(
            prefix="svt-schema-observe-", dir="/tmp"
        )
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.source = self.root / "release"
        self.source.mkdir()
        (self.source / "VERSION").write_text("0.5.21-hw\n")
        (self.source / "RELEASE_VERSION").write_text("0.5.21-hw\n")
        self.code = self.source / "app.py"
        self.code.write_text("SCHEMA_VERSION = 3\n")
        self.database = self.root / "data.sqlite3"
        self.create_database()

    def create_database(self) -> None:
        """Create required tables and fake indexes without application code."""
        with closing(sqlite3.connect(self.database)) as db:
            for table, fields in QA.EXPECTED.items():
                columns = ", ".join(
                    f'"{name}" TEXT' for name in sorted(fields)
                )
                db.execute(f'CREATE TABLE "{table}" ({columns})')
            db.execute(
                "INSERT INTO meta(key,value) VALUES('schema_version','3')"
            )
            db.execute(
                "INSERT INTO measurements(id,ts,voltage,frequency,phase) "
                "VALUES('1','1','222','50','L1')"
            )
            self.add_indexes(db)
            db.commit()

    @staticmethod
    def add_indexes(db: sqlite3.Connection) -> None:
        """Make the exactly named release index fixtures."""
        mapping = {
            "idx_measurements_ts": ("measurements", "ts"),
            "idx_measurements_phase_ts": ("measurements", "phase"),
            "idx_outages_started": ("outages", "started_at"),
            "idx_outages_phase_started": ("outages", "phase"),
            "idx_settings_audit_ts": ("settings_audit", "ts"),
        }
        for index, (table, field) in mapping.items():
            db.execute(f'CREATE INDEX "{index}" ON "{table}" ("{field}")')

    def result(self) -> dict:
        """Read a single synthetic source and database."""
        return QA.evaluate(self.source, self.database)

    def invoke(self, *args: str) -> tuple[int, str]:
        """Call CLI without printing internal QA paths."""
        out = io.StringIO()
        with (
            patch.object(sys, "argv", [str(SCRIPT), *args]),
            contextlib.redirect_stdout(out),
        ):
            return QA.main(), out.getvalue()

    def test_dry_run_does_not_access_host(self) -> None:
        """No arguments is inert and cannot accidentally probe production."""
        with patch.object(QA, "evaluate", side_effect=AssertionError):
            self.assertEqual(self.invoke()[0], 0)
            self.assertEqual(self.invoke("--dry-run")[0], 0)

    def test_exact_schema_observed_without_boot_permission(self) -> None:
        """Matching source/schema is evidence, not permission."""
        report = self.result()
        self.assertEqual(report["status"], "SCHEMA_OBSERVED_MATCH")
        self.assertEqual(report["candidate_schema_version"], 3)
        self.assertEqual(report["current_schema_version"], 3)
        self.assertFalse(report["schema_runtime_compatibility_proven"])
        self.assertFalse(report["boot_start_authorized"])
        self.assertFalse(report["production_modified"])

    def test_sqlite_header_version_is_not_application_marker(self) -> None:
        """PRAGMA user_version=0 must not override meta.schema_version."""
        report = self.result()
        self.assertEqual(report["sqlite_user_version"], 0)
        self.assertEqual(report["status"], "SCHEMA_OBSERVED_MATCH")

    def test_fresh_schema_has_no_write_side_effect(self) -> None:
        """Observing schema does not change the database contents."""
        before = self.database.read_bytes()
        self.assertEqual(self.result()["status"], "SCHEMA_OBSERVED_MATCH")
        self.assertEqual(self.database.read_bytes(), before)

    def test_newer_current_schema_requires_review(self) -> None:
        """Never permit a writer to downgrade future schema history."""
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("UPDATE meta SET value='4'")
            db.commit()
        report = self.result()
        self.assertIn("FUTURE_SCHEMA_DOWNGRADE_UNSAFE", report["reasons"])

    def test_older_schema_requires_migration_review(self) -> None:
        """Do not execute migrations as part of the read-only audit."""
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("UPDATE meta SET value='2'")
            db.commit()
        report = self.result()
        self.assertIn("SCHEMA_MIGRATION_REQUIRED", report["reasons"])
        self.assertFalse(report["schema_migration_executed"])

    def test_missing_meta_marker_is_blocked(self) -> None:
        """No guessing a schema version from SQLite header fields."""
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("DELETE FROM meta")
            db.commit()
        self.assertEqual(self.result()["status"], "BLOCKED")

    def test_bad_meta_marker_is_blocked(self) -> None:
        """Malformed release metadata is not a valid integer schema."""
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("UPDATE meta SET value='version3'")
            db.commit()
        self.assertEqual(self.result()["status"], "BLOCKED")

    def test_missing_required_phase_column_requires_review(self) -> None:
        """A reported version alone cannot hide missing columns."""
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("DROP INDEX idx_measurements_phase_ts")
            db.execute("ALTER TABLE measurements DROP COLUMN phase")
            db.commit()
        report = self.result()
        self.assertIn(
            "REQUIRED_COLUMNS_MISSING_MEASUREMENTS", report["reasons"]
        )

    def test_missing_required_index_requires_review(self) -> None:
        """Index presence is part of the observed release contract."""
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("DROP INDEX idx_measurements_phase_ts")
            db.commit()
        self.assertIn("REQUIRED_INDEXES_MISSING", self.result()["reasons"])

    def test_empty_history_requires_review(self) -> None:
        """An empty DB cannot represent a preserved monitoring history."""
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("DELETE FROM measurements")
            db.commit()
        self.assertIn("CURRENT_HISTORY_EMPTY", self.result()["reasons"])

    def test_additional_table_allowed_in_observation(self) -> None:
        """Extra known or future tables do not silently trigger migration."""
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("CREATE TABLE qa_extra(v TEXT)")
            db.commit()
        self.assertEqual(self.result()["status"], "SCHEMA_OBSERVED_MATCH")

    def test_source_and_release_version_mismatch_blocks(self) -> None:
        """Untrusted mixed release trees may not provide schema evidence."""
        (self.source / "RELEASE_VERSION").write_text("0.5.15-hw\n")
        self.assertEqual(self.result()["status"], "BLOCKED")

    def test_source_version_without_hw_format_blocks(self) -> None:
        """Require the expected signed release version shape."""
        (self.source / "VERSION").write_text("latest\n")
        self.assertEqual(self.result()["status"], "BLOCKED")

    def test_syntax_error_does_not_execute_code(self) -> None:
        """Only parse AST, never import or run potentially dangerous code."""
        self.code.write_text("SCHEMA_VERSION = (\n")
        self.assertEqual(self.result()["status"], "BLOCKED")

    def test_computed_schema_assignment_not_executed(self) -> None:
        """An arbitrary call expression must never execute during audit."""
        marker = self.root / "danger-marker"
        self.code.write_text(
            'SCHEMA_VERSION = __import__("pathlib").Path('
            + repr(str(marker))
            + ").touch()\n"
        )
        self.assertEqual(self.result()["status"], "BLOCKED")
        self.assertFalse(marker.exists())

    def test_redefined_schema_marker_blocks(self) -> None:
        """Ambiguous release source version is never trusted."""
        self.code.write_text("SCHEMA_VERSION=3\nSCHEMA_VERSION=4\n")
        self.assertEqual(self.result()["status"], "BLOCKED")

    def test_future_release_schema_marker_rejected(self) -> None:
        """Unknown schema major numbers require a new reviewed contract."""
        self.code.write_text("SCHEMA_VERSION=101\n")
        self.assertEqual(self.result()["status"], "BLOCKED")

    def test_source_symlink_is_blocked(self) -> None:
        """Do not follow redirected source version files."""
        marker = self.source / "VERSION"
        marker.unlink()
        marker.symlink_to(self.source / "RELEASE_VERSION")
        self.assertEqual(self.result()["status"], "BLOCKED")

    def test_source_world_writable_is_blocked(self) -> None:
        """A mutable source app.py is not trusted release evidence."""
        self.code.chmod(0o666)
        self.assertEqual(self.result()["status"], "BLOCKED")

    def test_database_symlink_is_blocked(self) -> None:
        """No path redirection to another SQLite database."""
        alias = self.root / "linked-db.sqlite3"
        alias.symlink_to(self.database)
        report = QA.evaluate(self.source, alias)
        self.assertEqual(report["status"], "BLOCKED")

    def test_database_missing_is_blocked(self) -> None:
        """Never create a replacement database in read-only mode."""
        self.database.unlink()
        self.assertEqual(self.result()["status"], "BLOCKED")
        self.assertFalse(self.database.exists())

    def test_corrupted_sqlite_is_blocked(self) -> None:
        """Corruption may not trigger a historical restore."""
        self.database.write_bytes(b"not a database")
        self.assertEqual(self.result()["status"], "BLOCKED")

    def test_cli_requires_both_explicit_paths(self) -> None:
        """No missing argument may fall back to installed production data."""
        code, _ = self.invoke("--source", str(self.source))
        self.assertEqual(code, 2)

    def test_cli_uses_only_supplied_private_paths(self) -> None:
        """Explicit fixture command cannot invoke any Docker operation."""
        with patch.object(subprocess, "run", side_effect=AssertionError):
            code, output = self.invoke(
                "--source",
                str(self.source),
                "--database",
                str(self.database),
            )
        self.assertEqual(code, 0)
        self.assertIn("SCHEMA_OBSERVED_MATCH", output)

    def test_denied_result_flags_never_authorize_action(self) -> None:
        """Any schema error remains non-authorizing."""
        self.code.unlink()
        report = self.result()
        self.assertEqual(report["status"], "BLOCKED")
        for key in (
            "schema_migration_executed",
            "signature_verified_here",
            "schema_runtime_compatibility_proven",
            "boot_start_authorized",
            "source_install_authorized",
            "telegram_photo_authorized",
            "production_modified",
        ):
            self.assertFalse(report[key])

    def test_cli_help_is_available(self) -> None:
        """Support -h and --help without opening SQLite."""
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("--database", proc.stdout)


if __name__ == "__main__":
    unittest.main()
