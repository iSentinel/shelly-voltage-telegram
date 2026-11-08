"""Fail closed when EM-50 keeps writing during a deployment snapshot."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_em50_predeploy_backup_guard import make_db

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "em50_writer_quiescence",
    ROOT / "scripts/em50-writer-quiescence-preflight.py",
)
assert SPEC and SPEC.loader
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)


class WriterQuiescenceTests(unittest.TestCase):
    """Never approve a release while physical samples are advancing."""

    def setUp(self) -> None:
        """Build a disposable SQLite fixture, never production data."""

        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = Path(temp.name) / "measurements.sqlite3"
        make_db(self.db)

    def run_offline_check(self, change=None) -> str:
        """Run both observations with a mocked stopped Docker writer."""

        with (
            patch.object(QA, "container_running", return_value=False),
            patch.object(QA.time, "sleep", side_effect=change),
        ):
            return QA.check_window(self.db, "synthetic", 1)

    def change_db(self, sql: str, values: tuple = ()) -> None:
        """Apply one isolated write to prove a concurrent-source change."""

        with contextlib.closing(sqlite3.connect(self.db)) as connection:
            connection.execute(sql, values)
            connection.commit()

    def test_stopped_writer_and_unchanged_history_can_pass(self):
        """A still source may pass a prerequisite, not release approval."""

        result = self.run_offline_check()
        self.assertIn("history_stable=true", result)
        self.assertIn("release_authorized=false", result)

    def test_running_producer_blocks_before_sleep_and_sqlite(self):
        """The real active bot must never be considered quiescent."""

        with (
            patch.object(QA, "container_running", return_value=True),
            patch.object(QA, "snapshot_state") as snapshot,
            patch.object(QA.time, "sleep") as sleep,
            self.assertRaisesRegex(ValueError, "running"),
        ):
            QA.check_window(self.db, "synthetic", 1)
        snapshot.assert_not_called()
        sleep.assert_not_called()

    def test_writer_restart_during_observation_blocks(self):
        """A stopped writer that restarts invalidates an observation."""

        with (
            patch.object(QA, "container_running", side_effect=[False, True]),
            patch.object(QA.time, "sleep"),
            self.assertRaisesRegex(ValueError, "restarted"),
        ):
            QA.check_window(self.db, "synthetic", 1)

    def test_new_measurement_between_checks_blocks(self):
        """Physical poll resumption must invalidate release readiness."""

        def advance(_seconds: int) -> None:
            """Add a fresh L1 measurement to the synthetic database."""

            self.change_db(
                "INSERT INTO measurements VALUES (?,?,?,?,?)",
                (4, 4.0, 216.2, 50.0, "L1"),
            )

        with self.assertRaisesRegex(ValueError, "changed"):
            self.run_offline_check(advance)

    def test_old_measurement_edit_with_same_count_blocks(self):
        """Complete-history hash catches modifications to earlier rows."""

        def change_old(_seconds: int) -> None:
            """Edit a row without changing the number of measurements."""

            self.change_db(
                "UPDATE measurements SET voltage=? WHERE id=?",
                (243.0, 1),
            )

        with self.assertRaisesRegex(ValueError, "changed"):
            self.run_offline_check(change_old)

    def test_outage_history_edit_blocks(self):
        """A changed old outage is not safe for restoring history."""

        def modify_outage(_seconds: int) -> None:
            """Simulate outage closure metadata changing mid-observation."""

            self.change_db(
                "UPDATE outages SET ended_at=? WHERE id=?",
                (12.0, 18),
            )

        with self.assertRaisesRegex(ValueError, "changed"):
            self.run_offline_check(modify_outage)

    def test_telegram_status_rotation_blocks(self):
        """New status-message identifiers cannot be lost during rollback."""

        def rotate(_seconds: int) -> None:
            """Change only a private status-message reference."""

            self.change_db(
                "UPDATE meta SET value=? WHERE key=?",
                ("new-message-private", "telegram_status_message_id"),
            )

        with self.assertRaisesRegex(ValueError, "changed"):
            self.run_offline_check(rotate)

    def test_missing_status_metadata_fails_closed(self):
        """Empty saved status is not proof of a complete recoverable state."""

        self.change_db("DELETE FROM meta")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.run_offline_check()

    def test_observation_duration_cannot_be_zero(self):
        """A zero-duration check cannot establish a stable writer window."""

        with (
            patch.object(QA, "container_running") as inspect,
            self.assertRaisesRegex(ValueError, "1 to 60"),
        ):
            QA.check_window(self.db, "synthetic", 0)
        inspect.assert_not_called()

    def test_dry_run_has_no_network_docker_or_database_access(self):
        """Default documentation commands cannot alter production."""

        args = SimpleNamespace(dry_run=True, check=False)
        with (
            patch.object(
                QA.argparse.ArgumentParser,
                "parse_args",
                return_value=args,
            ),
            patch.object(QA, "container_running") as docker,
            patch.object(QA, "snapshot_state") as database,
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(QA.main(), 0)
        self.assertIn("no_writes=true", output.getvalue())
        docker.assert_not_called()
        database.assert_not_called()

    def test_failure_never_reveals_sensitive_container_name(self):
        """Errors must not leak arguments or subprocess JSON."""

        args = SimpleNamespace(
            dry_run=False,
            check=True,
            container="private-user-id-123",
            db=str(self.db),
            observe_seconds=1,
        )
        with (
            patch.object(
                QA.argparse.ArgumentParser,
                "parse_args",
                return_value=args,
            ),
            patch.object(
                QA, "container_running", side_effect=ValueError("private")
            ),
            contextlib.redirect_stderr(io.StringIO()) as output,
        ):
            self.assertEqual(QA.main(), 1)
        self.assertIn("prerequisite_not_met", output.getvalue())
        self.assertNotIn("private-user-id-123", output.getvalue())


if __name__ == "__main__":
    unittest.main()
