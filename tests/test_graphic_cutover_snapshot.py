"""Private A/B snapshot staging, approval and parity checks."""

from __future__ import annotations

import argparse
import importlib.util
import os
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/graphic-cutover-snapshot.py"
SPEC = importlib.util.spec_from_file_location("graphic_snapshot", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
SNAP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SNAP)


class GraphicCutoverSnapshotTests(unittest.TestCase):
    """Create private archives without touching live Shelly or Telegram."""

    def setUp(self):
        """Prepare an exact-release source and tiny historical SQLite."""

        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.source = self.folder / "installed"
        self.source.mkdir()
        self.make_source_fixture()
        private_data = self.folder / "sqlite-data"
        private_data.mkdir()
        self.database = private_data / "history.sqlite3"
        self.make_db()
        self.archive_root = self.folder / "graphic-cutover"
        self.args = argparse.Namespace(
            phase="A",
            source=self.source,
            database=self.database,
            owner_approval_file=self.folder / "owner.json",
            output_root=self.archive_root,
            execute=False,
            jsonl=True,
        )

    def make_source_fixture(self) -> None:
        """Seed minimum archive inputs and deliberate excluded secrets."""

        files = (
            "VERSION",
            "app.py",
            "Dockerfile",
            "compose.yaml",
            "requirements.txt",
            "scripts/backup.sh",
            "systemd/shelly-voltage-compose.service",
            "systemd/shelly-voltage-backup.service",
        )
        for name in files:
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                "0.5.15-hw\n" if name == "VERSION" else f"fixture={name}\n"
            )
        (self.source / ".env").write_text("TOP_SECRET=never_archive\n")
        (self.source / "secrets").mkdir()
        (self.source / "secrets/token").write_text("secret-token")

    def make_db(self):
        """Record real rows and metadata without a running writer."""

        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute(
                "create table measurements("
                "id integer primary key,ts real,voltage real,phase text)"
            )
            db.execute("create table meta(key text primary key,value text)")
            db.execute("insert into measurements values (1,1000,220.3,'L1')")
            db.execute(
                "insert into meta values ('telegram_status_message_id','101')"
            )

    def fake_guards(self, stopped=True, approved=True):
        """Replace only external state checks with explicit return values."""

        def resolve(filename, alias):
            if "rollout-readiness" in filename:
                return SimpleNamespace(
                    owner_approval_verified=lambda _: approved
                )
            if "confirm-legacy-text" in filename:
                return SimpleNamespace(writer_stopped=stopped)
            raise AssertionError("unrecognized guarded module")

        return mock.patch.object(SNAP, "module", side_effect=resolve)

    def set_marker(self, value: str):
        """Simulate a verified text receipt in local SQLite only."""

        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute(
                "insert or replace into meta(key,value) values (?,?)",
                ("telegram_status_media_identity", value),
            )

    def test_backup_root_must_not_overlap_source_or_live_sqlite(self):
        """Reject recursive archives and backup paths inside live data."""

        candidates = (
            self.source,
            self.source / "backups",
            self.database.parent,
            self.database.parent / "backups",
            self.folder,
        )
        for output in candidates:
            with self.subTest(output=output):
                self.args.output_root = output
                with (
                    self.fake_guards(stopped=lambda: True),
                    self.assertRaisesRegex(
                        SNAP.SnapshotBlocked, "BACKUP_OVERLAPS_LIVE_DATA"
                    ),
                ):
                    SNAP.run(self.args)
                self.assertFalse(
                    (self.source / ".graphic-stage-test").exists()
                )

    def test_live_writer_is_blocked_without_creating_backup_path(self):
        """Running production must not create A or B snapshots."""

        self.args.execute = True
        with (
            self.fake_guards(stopped=lambda: False),
            self.assertRaisesRegex(SNAP.SnapshotBlocked, "WRITER_NOT_STOPPED"),
        ):
            SNAP.run(self.args)
        self.assertFalse(self.archive_root.exists())

    def test_phase_a_requires_execute_and_excludes_private_config(self):
        """A captures the original text state and complete history."""

        with self.fake_guards(stopped=lambda: True):
            plan = SNAP.run(self.args)
            self.assertEqual(plan["status"], "DRY_RUN_PASS")
            self.assertFalse(self.archive_root.exists())
            self.args.execute = True
            created = SNAP.run(self.args)
        path = Path(created["archive"])
        self.assertEqual(created["measurement_rows"], 1)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.archive_root.stat().st_mode), 0o700)
        gate = SNAP.module("graphic-rollout-readiness.py", "test_sha_guard")
        self.assertTrue(gate.verify_sha256(path / "source.tar.gz"))
        self.assertTrue(
            gate.verify_sha256(path / "voltage-cutover.sqlite3.gz")
        )
        self.assertEqual(gate.snapshot_issues(path, 2), [])
        self.assertEqual(
            gate.version_issues(path, self.source / "VERSION"), []
        )
        self.assertIn("cutover_phase=A", (path / "manifest.env").read_text())
        with tarfile.open(path / "source.tar.gz", "r:gz") as tar:
            members = tar.getnames()
        self.assertFalse(any("secrets" in name for name in members))
        self.assertFalse(any(name.endswith("/.env") for name in members))

    def test_phase_b_requires_exact_confirmed_text_identity(self):
        """B includes new metadata; A then fails the whole SQLite digest."""

        self.args.execute = True
        with self.fake_guards(stopped=lambda: True):
            phase_a = Path(SNAP.run(self.args)["archive"])
        self.set_marker("text:-100123:101")
        self.args.phase = "B"
        with (
            mock.patch.dict(
                os.environ, {"TELEGRAM_STATUS_CHAT_ID": "-100123"}
            ),
            self.fake_guards(stopped=lambda: True),
        ):
            phase_b = Path(SNAP.run(self.args)["archive"])
        self.assertNotEqual(phase_a, phase_b)
        validator = ROOT / "scripts/validate-sqlite-archive.py"

        def compare(path):
            return subprocess.run(
                [
                    sys.executable,
                    str(validator),
                    "--archive",
                    str(path),
                    "--compare-db",
                    str(self.database),
                ],
                capture_output=True,
                timeout=15,
                check=False,
            ).returncode

        self.assertEqual(compare(phase_a / "voltage-cutover.sqlite3.gz"), 2)
        self.assertEqual(compare(phase_b / "voltage-cutover.sqlite3.gz"), 0)
        gate = SNAP.module("graphic-rollout-readiness.py", "test_ab_parity")
        self.assertEqual(
            gate.snapshot_parity_issues(phase_b, self.database), []
        )

    def test_photo_marker_is_not_a_valid_phase_b(self):
        """Photo/unknown media is never eligible for B confirmation."""

        self.args.phase = "B"
        self.set_marker("photo:-100123:101")
        with (
            mock.patch.dict(
                os.environ, {"TELEGRAM_STATUS_CHAT_ID": "-100123"}
            ),
            self.fake_guards(stopped=lambda: True),
            self.assertRaisesRegex(
                SNAP.SnapshotBlocked, "CONFIRMED_TEXT_IDENTITY_REQUIRED"
            ),
        ):
            SNAP.run(self.args)
        self.assertFalse(self.archive_root.exists())

    def test_resumed_writer_discards_incomplete_snapshot(self):
        """Writer restart between preflight and archive must clean staging."""

        self.args.execute = True
        writer = mock.Mock(side_effect=[True, False])
        with (
            self.fake_guards(stopped=writer),
            self.assertRaisesRegex(SNAP.SnapshotBlocked, "WRITER_RESTARTED"),
        ):
            SNAP.run(self.args)
        self.assertEqual(list(self.archive_root.iterdir()), [])

    def test_writer_restarts_after_validation_before_publication(self):
        """Last-moment writer restart must prevent an apparently valid B."""

        self.args.execute = True
        writer = mock.Mock(side_effect=[True, True, False])
        with (
            self.fake_guards(stopped=writer),
            self.assertRaisesRegex(SNAP.SnapshotBlocked, "WRITER_RESTARTED"),
        ):
            SNAP.run(self.args)
        self.assertEqual(list(self.archive_root.iterdir()), [])

    def test_metadata_mutation_after_first_validation_discards_archive(self):
        """Full logical parity also detects a metadata-only late write."""

        self.args.execute = True
        real_validate = SNAP.validate
        calls = [0]

        def change_after_first(*args):
            real_validate(*args)
            calls[0] += 1
            if calls[0] == 1:
                with closing(sqlite3.connect(self.database)) as db, db:
                    db.execute(
                        "insert into meta values (?,?)",
                        ("live_setting_changed", "unexpected"),
                    )

        with (
            self.fake_guards(stopped=lambda: True),
            mock.patch.object(
                SNAP, "validate", side_effect=change_after_first
            ),
            self.assertRaisesRegex(
                SNAP.SnapshotBlocked, "SNAPSHOT_VALIDATION_FAILED"
            ),
        ):
            SNAP.run(self.args)
        self.assertEqual(calls[0], 1)
        self.assertEqual(list(self.archive_root.iterdir()), [])

    def test_owner_rejected_or_linked_output_root_blocks(self):
        """Approvals and safe output directories are mandatory."""

        with (
            self.fake_guards(stopped=lambda: True, approved=False),
            self.assertRaisesRegex(
                SNAP.SnapshotBlocked, "OWNER_APPROVAL_INVALID"
            ),
        ):
            SNAP.run(self.args)
        link = self.folder / "linked-output"
        link.symlink_to(self.folder / "other")
        self.args.output_root = link
        with (
            self.fake_guards(stopped=lambda: True),
            self.assertRaisesRegex(SNAP.SnapshotBlocked, "PATH_UNSAFE"),
        ):
            SNAP.run(self.args)


if __name__ == "__main__":
    unittest.main()
