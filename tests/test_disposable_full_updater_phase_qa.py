"""Fail-closed synthetic full updater phase QA; no production operations."""

from __future__ import annotations

import contextlib
import fcntl
import gzip
import hashlib
import importlib.util
import io
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/disposable-full-updater-phase-qa.py"
)
SPEC = importlib.util.spec_from_file_location("full_candidate_qa", SCRIPT)
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)


class FullUpdaterPhaseQATests(unittest.TestCase):
    """Only private synthetic SQLite and mocked service interactions."""

    def setUp(self) -> None:
        """Create owned SQLite and stub candidate source files."""
        tmp = tempfile.TemporaryDirectory(
            prefix="svt-full-updater-unit-", dir="/tmp"
        )
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.original = self.root / "synthetic.sqlite3"
        self.source = self.root / "candidate"
        self.source.mkdir()
        with contextlib.closing(sqlite3.connect(self.original)) as db, db:
            db.execute(
                "CREATE TABLE measurements("
                "id INTEGER PRIMARY KEY,ts REAL,voltage REAL,"
                "frequency REAL,phase TEXT)"
            )
            db.execute("INSERT INTO measurements VALUES(1,1,225.0,50.0,'L1')")
        for filename in QA.PINNED:
            (self.source / filename).write_text("QA_SCRIPT_NOT_EXECUTED\n")
        for key, value in (
            ("ORIGINAL", self.original),
            ("SOURCE", self.source),
        ):
            context = patch.object(QA, key, value)
            context.start()
            self.addCleanup(context.stop)

    def cli(self, *args: str) -> tuple[int, str]:
        """Collect one CLI response without invoking real QA."""
        text = io.StringIO()
        with (
            patch.object(sys, "argv", [str(SCRIPT), *args]),
            contextlib.redirect_stdout(text),
        ):
            return QA.main(), text.getvalue()

    def test_default_does_not_inspect_guest(self) -> None:
        """Default CLI must not touch SQLite, Docker or systemd."""
        with patch.object(QA, "guest_only", side_effect=AssertionError):
            code, output = self.cli()
        self.assertEqual(code, 0)
        self.assertIn("DRY-RUN", output)

    def test_explicit_dry_run_is_inert(self) -> None:
        """--dry-run dominates --run-disposable."""
        with patch.object(QA, "guest_only", side_effect=AssertionError):
            code, _ = self.cli("--dry-run", "--run-disposable")
        self.assertEqual(code, 0)

    def test_wrong_approval_refused(self) -> None:
        """Generic yes does not run even an isolated updater."""
        with patch.object(QA, "guest_only", side_effect=AssertionError):
            code, output = self.cli("--run-disposable", "--confirm", "yes")
        self.assertEqual(code, 2)
        self.assertIn("FAIL", output)

    def test_help_available(self) -> None:
        """Both help spellings have no network or guest requirement."""
        for flag in ("-h", "--help"):
            with self.subTest(flag=flag):
                command = subprocess.run(
                    [sys.executable, str(SCRIPT), flag],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                self.assertEqual(command.returncode, 0)
                self.assertIn("--confirm", command.stdout)

    def test_guest_wrong_hostname_rejected(self) -> None:
        """Host allowlist cannot be bypassed."""
        with (
            patch.object(QA.os, "geteuid", return_value=0),
            patch.object(
                QA.os,
                "uname",
                return_value=SimpleNamespace(nodename="production"),
            ),
            self.assertRaisesRegex(RuntimeError, "QA_GUEST_ONLY"),
        ):
            QA.guest_only()

    def test_guest_external_network_rejected(self) -> None:
        """Any non-loopback NIC is a hard blocker."""
        with (
            patch.object(QA.os, "geteuid", return_value=0),
            patch.object(
                QA.os,
                "uname",
                return_value=SimpleNamespace(
                    nodename="shelly-docker-reboot-qa"
                ),
            ),
            patch.object(
                QA.socket,
                "if_nameindex",
                return_value=[(1, "lo"), (2, "eth0")],
            ),
            self.assertRaisesRegex(RuntimeError, "EXTERNAL_NETWORK_PRESENT"),
        ):
            QA.guest_only()

    def test_source_checksum_mismatch_rejected(self) -> None:
        """Never source a different candidate release implementation."""
        with (
            patch.object(QA.os, "geteuid", return_value=0),
            patch.object(
                QA.os,
                "uname",
                return_value=SimpleNamespace(
                    nodename="shelly-docker-reboot-qa"
                ),
            ),
            patch.object(QA.socket, "if_nameindex", return_value=[(1, "lo")]),
            self.assertRaisesRegex(RuntimeError, "STAGED_SOURCE_SHA_MISMATCH"),
        ):
            QA.guest_only()

    def test_source_missing_rejected(self) -> None:
        """Missing pinned input cannot silently be fabricated."""
        (self.source / "apply-update.sh").unlink()
        with (
            patch.object(QA.os, "geteuid", return_value=0),
            patch.object(
                QA.os,
                "uname",
                return_value=SimpleNamespace(
                    nodename="shelly-docker-reboot-qa"
                ),
            ),
            patch.object(QA.socket, "if_nameindex", return_value=[(1, "lo")]),
            self.assertRaisesRegex(RuntimeError, "STAGED_SOURCE_INVALID"),
        ):
            QA.guest_only()

    def test_valid_hashes_running_writer_rejected(self) -> None:
        """A running writer always blocks the full synthetic transaction."""
        digests = {
            key: hashlib.sha256(path.read_bytes()).hexdigest()
            for key in QA.PINNED
            for path in [self.source / key]
        }
        with (
            patch.object(QA, "PINNED", digests),
            patch.object(QA.os, "geteuid", return_value=0),
            patch.object(
                QA.os,
                "uname",
                return_value=SimpleNamespace(
                    nodename="shelly-docker-reboot-qa"
                ),
            ),
            patch.object(QA.socket, "if_nameindex", return_value=[(1, "lo")]),
            patch.object(
                QA.subprocess,
                "run",
                return_value=SimpleNamespace(
                    stdout="true|no|none\n", returncode=0
                ),
            ),
            self.assertRaisesRegex(RuntimeError, "QA_WRITER_NOT_STOPPED"),
        ):
            QA.guest_only()

    def test_build_environment_has_private_paths(self) -> None:
        """Mocked child may address only the scratch release directories."""
        env = QA.build_environment(self.root, "stale")
        self.assertEqual(env["SVT_ROOT"], str(self.root))
        self.assertEqual(env["QA_EVENT_MODE"], "stale")
        self.assertIn(str(self.root), env["DATABASE_PATH"])
        self.assertEqual(env["QA_DIGEST"], QA.DIGEST)

    def test_invalid_mode_refused(self) -> None:
        """Only clean and stale are authorized private experiments."""
        with self.assertRaisesRegex(RuntimeError, "INVALID_QA_SCENARIO"):
            QA.run_one("production")

    def test_wait_for_start_accepts_marker(self) -> None:
        """The real Bash start barrier is an explicit filesystem event."""
        (self.root / "start-stage").write_text("qa\n")
        QA.wait_for_start(self.root, SimpleNamespace(poll=lambda: None))

    def test_wait_for_start_early_exit_blocks(self) -> None:
        """Child process exit before pending is not a successful test."""
        with self.assertRaisesRegex(RuntimeError, "UPDATER_EXITED_EARLY"):
            QA.wait_for_start(self.root, SimpleNamespace(poll=lambda: 77))

    def test_lock_probe_separate_shared_block(self) -> None:
        """Independent process cannot cross our B maintenance EX lock."""
        lock = self.root / "private-B.lock"
        lock.touch(mode=0o600)
        with lock.open("rb") as holder:
            fcntl.flock(holder, fcntl.LOCK_EX)
            self.assertEqual(QA.probe(lock, fcntl.LOCK_SH), "BLOCKED")
        self.assertEqual(QA.probe(lock, fcntl.LOCK_SH), "ACQUIRED")

    def test_prepare_only_copies_synthetic_database(self) -> None:
        """Fixture contains exactly one invented row, never live data."""
        target = self.root / "fixture"
        target.mkdir(mode=0o700)
        self.assertEqual(QA.prepare(target), 1)
        with contextlib.closing(
            sqlite3.connect(target / "data/current.sqlite3")
        ) as db:
            count = db.execute("SELECT COUNT(*) FROM measurements").fetchone()[
                0
            ]
        self.assertEqual(count, 1)
        self.assertTrue((target / "scripts/start-compose.sh").exists())
        self.assertFalse((target / "state/pending-update").exists())

    def test_archived_rows_checksum_and_sqlite(self) -> None:
        """A private gzip archive can prove full-snapshot row count."""
        path = self.root / "state/snapshots/QA/voltage-qa.sqlite3.gz"
        path.parent.mkdir(parents=True)
        with self.original.open("rb") as db, gzip.open(path, "wb") as out:
            out.write(db.read())
        Path(str(path) + ".sha256").write_text(
            hashlib.sha256(path.read_bytes()).hexdigest() + "  private\n"
        )
        self.assertEqual(QA.archived_rows(self.root), 1)

    def test_corrupt_snapshot_checksum_rejected(self) -> None:
        """Checksums fail closed before accepting a rollback snapshot."""
        path = self.root / "state/snapshots/QA/voltage-qa.sqlite3.gz"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"bad archive")
        Path(str(path) + ".sha256").write_text("0" * 64 + "  private\n")
        with self.assertRaisesRegex(RuntimeError, "SNAPSHOT_SHA_MISMATCH"):
            QA.archived_rows(self.root)

    def prepare_outcome(self, stale: bool) -> Path:
        """Build an invented current DB and private pending state."""
        fixture = self.root / "result"
        (fixture / "data").mkdir(parents=True)
        (fixture / "state").mkdir()
        (fixture / "events").write_text(
            "requirements_a_held\nSIGNATURE_MOCKED_OFFLINE\n"
            "SOURCE_FETCH_MOCKED_OFFLINE\n"
            "DOCKER_PULL_MOCKED_OFFLINE\nstart_stack_pending_seen\n"
        )
        with (
            contextlib.closing(sqlite3.connect(self.original)) as source,
            contextlib.closing(
                sqlite3.connect(fixture / "data/current.sqlite3")
            ) as database,
        ):
            source.backup(database)
        if stale:
            with (
                contextlib.closing(
                    sqlite3.connect(fixture / "data/current.sqlite3")
                ) as db,
                db,
            ):
                db.execute(
                    "INSERT INTO measurements VALUES(2,2,221.7,50.0,'L1')"
                )
            (fixture / "state/pending-update").write_text("QA\n")
            (fixture / "state/override.yaml").write_text("services:\n")
        else:
            (fixture / "state/deployed.json").write_text("{}")
        return fixture

    def test_clean_outcome_preserves_history(self) -> None:
        """Successful candidate main cannot drop the snapshot history."""
        fixture = self.prepare_outcome(False)
        with patch.object(QA, "archived_rows", return_value=1):
            result = QA.outcome(fixture, "clean", 1, 0)
        self.assertEqual(result["after"], 1)
        self.assertFalse(result["pending"])

    def test_stale_outcome_retains_all_new_rows(self) -> None:
        """Failed restart must keep both WAL row and pending marker."""
        fixture = self.prepare_outcome(True)
        with patch.object(QA, "archived_rows", return_value=1):
            result = QA.outcome(fixture, "stale", 1, 77)
        self.assertEqual(result["after"], 2)
        self.assertTrue(result["pending"])

    def test_stale_rollback_dropping_row_fails(self) -> None:
        """Explicit regression prevents a green result after data loss."""
        fixture = self.prepare_outcome(False)
        with self.assertRaises(AssertionError):
            QA.outcome(fixture, "stale", 1, 77)

    def test_cli_guest_failure_is_exit_three(self) -> None:
        """Wrong host cannot produce an accidental PASS."""
        with patch.object(
            QA, "guest_only", side_effect=RuntimeError("TEST_QA_BLOCKED")
        ):
            code, output = self.cli(
                "--run-disposable", "--confirm", QA.CONFIRM
            )
        self.assertEqual(code, 3)
        self.assertIn("QA_FULL_UPDATER_BLOCKED", output)

    def test_cli_json_keeps_external_phases_mocked(self) -> None:
        """A green result never claims real signature or Compose checks."""
        with (
            patch.object(QA, "guest_only"),
            patch.object(
                QA,
                "run_one",
                side_effect=[{"case": "clean"}, {"case": "stale"}],
            ),
        ):
            code, output = self.cli(
                "--run-disposable", "--confirm", QA.CONFIRM
            )
        self.assertEqual(code, 0)
        payload = json.loads(output)
        self.assertEqual(
            payload["real_signature_verification"], "MOCKED_OFFLINE"
        )
        self.assertFalse(payload["production_modified"])
        self.assertEqual(len(payload["cases"]), 2)


if __name__ == "__main__":
    unittest.main()
