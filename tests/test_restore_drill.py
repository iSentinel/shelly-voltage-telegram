"""Offline backup restoration smoke checks using synthetic local SQLite."""

from __future__ import annotations

import gzip
import hashlib
import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

from test_app import APP

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/restore-drill.py"
SPEC = importlib.util.spec_from_file_location("restore_drill_tested", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("missing restore drill")
DRILL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DRILL)


class RestoreDrillTests(unittest.TestCase):
    """Exercise valid and invalid archives without a real device."""

    def setUp(self):
        """Prepare a local archive directory with no shared files."""

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def archive(self, name: str, corrupt: bool = False) -> Path:
        """Write one compressed backup with checksum sidecar."""

        source = self.root / "source.sqlite3"
        storage = APP.Storage(source, "Europe/Kyiv")
        storage.initialize()
        storage.add(1000.0, 219.5, 50.0)
        content = source.read_bytes() if not corrupt else b"broken-SQLite"
        target = self.root / f"voltage-{name}.sqlite3.gz"
        target.write_bytes(gzip.compress(content))
        checksum = hashlib.sha256(target.read_bytes()).hexdigest()
        Path(f"{target}.sha256").write_text(f"{checksum}  {target.name}\n")
        return target

    def test_valid_backup_restores_state_but_not_readiness(self):
        """A preserved measurement must not count as fresh network data."""

        archive = self.archive("good")
        self.assertTrue(DRILL.restore_one(1, archive, 64 * 1024 * 1024))

    def legacy_archive(self, version: int) -> Path:
        """Create an authentic v1/v2-shaped compressed SQLite fixture."""

        source = self.root / "legacy.sqlite3"
        with closing(sqlite3.connect(source)) as db, db:
            db.execute(
                "CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)"
            )
            db.execute(
                "INSERT INTO meta VALUES('schema_version',?)", (str(version),)
            )
            db.execute(
                "CREATE TABLE measurements(id INTEGER PRIMARY KEY,"
                "ts REAL NOT NULL,voltage REAL NOT NULL,"
                "frequency REAL NOT NULL)"
            )
            db.execute("INSERT INTO measurements VALUES(1,1000,219.5,50)")
            if version == 2:
                db.execute(
                    "CREATE TABLE outages(id INTEGER PRIMARY KEY,"
                    "started_at REAL NOT NULL,ended_at REAL)"
                )
        target = self.root / f"voltage-legacy-v{version}.sqlite3.gz"
        target.write_bytes(gzip.compress(source.read_bytes()))
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        Path(f"{target}.sha256").write_text(f"{digest}  {target.name}\n")
        return target

    def test_legacy_v1_restores_via_disposable_migration(self):
        """Old v1 backup without phase/outages must restore L1 offline."""

        archive = self.legacy_archive(1)
        self.assertTrue(DRILL.restore_one(1, archive, 64 * 1024 * 1024))

    def test_legacy_v2_restores_via_disposable_migration(self):
        """Old v2 backup without phase is migratable for physical L1."""

        archive = self.legacy_archive(2)
        self.assertTrue(DRILL.restore_one(1, archive, 64 * 1024 * 1024))

    def test_legacy_migration_preserves_original_archive_checksum(self):
        """Migration of a temp database must never alter its gzip source."""

        archive = self.legacy_archive(1)
        before = hashlib.sha256(archive.read_bytes()).hexdigest()
        self.assertTrue(DRILL.restore_one(1, archive, 64 * 1024 * 1024))
        self.assertEqual(
            before, hashlib.sha256(archive.read_bytes()).hexdigest()
        )

    def test_restore_config_cannot_inherit_live_credentials(self):
        """Production tokens, file paths and phase hosts stay isolated."""

        archive = self.archive("isolation")
        secret = self.root / "secret-token"
        secret.write_text("fake-token")
        hostile = {
            "SHELLY_HOST": "10.0.0.77",
            "SHELLY_PASSWORD_FILE": str(secret),
            "TELEGRAM_BOT_TOKEN_FILE": str(secret),
            "TELEGRAM_CHAT_ID": "123",
            "SHELLY_METER_TYPE": "invalid-live-device-type",
            "PHASE_L2_HOST": "10.0.0.78",
            "PHASE_L3_HOST": "10.0.0.79",
        }
        with (
            patch.dict(os.environ, hostile),
            patch.object(
                Path,
                "read_text",
                side_effect=AssertionError("live secret unexpectedly read"),
            ),
        ):
            cfg = DRILL.isolated_config(self.root / "scratch.sqlite3")
            self.assertEqual(cfg.shelly_host, "192.0.2.1")
            self.assertEqual(cfg.shelly_meter_type, "pro_em50")
            self.assertEqual(cfg.shelly_password, "")
            self.assertEqual(cfg.telegram_token, "")
            self.assertEqual(cfg.telegram_chat_id, "")
            self.assertEqual(cfg.phase_l2_host, "")
            self.assertEqual(cfg.phase_l3_host, "")
            self.assertTrue(DRILL.restore_one(1, archive, 64 * 1024 * 1024))

    def test_unsupported_future_schema_fails_cleanly(self):
        """Future schema must FAIL with stable CLI code, not a traceback."""

        archive = self.archive("future-schema")
        source = self.root / "source.sqlite3"
        with closing(sqlite3.connect(source)) as db, db:
            db.execute(
                "INSERT OR REPLACE INTO meta(key,value) "
                "VALUES('schema_version','999')"
            )
        archive.write_bytes(gzip.compress(source.read_bytes()))
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        Path(f"{archive}.sha256").write_text(f"{digest}  {archive.name}\n")
        before = hashlib.sha256(archive.read_bytes()).hexdigest()
        self.assertFalse(DRILL.restore_one(1, archive, 64 * 1024 * 1024))
        self.assertEqual(
            before, hashlib.sha256(archive.read_bytes()).hexdigest()
        )

    def test_future_version_cli_returns_exit_two(self):
        """Unknown future migrations must not abort a batch trace."""

        archive = self.archive("future-cli")
        source = self.root / "source.sqlite3"
        with closing(sqlite3.connect(source)) as db, db:
            db.execute(
                "UPDATE meta SET value='999' WHERE key='schema_version'"
            )
        archive.write_bytes(gzip.compress(source.read_bytes()))
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        Path(f"{archive}.sha256").write_text(f"{digest}  {archive.name}\n")
        from contextlib import redirect_stdout
        from io import StringIO

        output = StringIO()
        with redirect_stdout(output):
            code = DRILL.main(
                [
                    "--backup-dir",
                    str(self.root),
                    "--count",
                    "1",
                    "--jsonl",
                ]
            )
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.getvalue())["status"], "fail")

    def test_insufficient_temp_space_fails_before_decompression(self):
        """Do not fill the LXC if scratch space cannot fit the size cap."""

        archive = self.archive("headroom-fail")
        limit = 64 * 1024 * 1024
        with (
            patch.object(
                DRILL.shutil,
                "disk_usage",
                return_value=Mock(
                    free=limit + 32 * 1024 * 1024 - 1,
                ),
            ),
            patch.object(
                DRILL.VERIFY,
                "unpack_archive",
                side_effect=AssertionError("archive unexpectedly extracted"),
            ) as unpack,
        ):
            self.assertFalse(DRILL.restore_one(1, archive, limit))
        unpack.assert_not_called()

    def test_scratch_headroom_accepts_exact_safety_boundary(self):
        """The configured cap and reserved margin must both fit."""

        limit = 64 * 1024 * 1024
        with patch.object(
            DRILL.shutil,
            "disk_usage",
            return_value=Mock(free=limit + 32 * 1024 * 1024),
        ):
            DRILL.ensure_scratch_headroom(self.root, limit)

    def test_scratch_space_read_failure_is_clean_restore_failure(self):
        """Unreadable temporary filesystem metadata must fail safely."""

        archive = self.archive("headroom-stat-error")
        with patch.object(
            DRILL.shutil,
            "disk_usage",
            side_effect=OSError("private scratch details"),
        ):
            self.assertFalse(DRILL.restore_one(1, archive, 64 * 1024 * 1024))

    def test_checksum_mismatch_is_rejected(self):
        """Do not restore a modified or incomplete archive."""

        archive = self.archive("bad-checksum")
        archive.write_bytes(archive.read_bytes() + b"unexpected")
        self.assertFalse(DRILL.restore_one(1, archive, 64 * 1024 * 1024))

    def test_corrupt_sqlite_is_rejected(self):
        """A valid gzip and checksum do not guarantee a valid SQLite DB."""

        archive = self.archive("bad-db", corrupt=True)
        self.assertFalse(DRILL.restore_one(1, archive, 64 * 1024 * 1024))

    def test_bounded_decompression_rejects_large_archive(self):
        """The drill must fail closed on a too-small extraction limit."""

        archive = self.archive("too-big")
        self.assertFalse(DRILL.restore_one(1, archive, 12))

    def test_cli_writes_one_success_summary(self):
        """Produce one stable JSON result for CI or unattended runs."""

        self.archive("good")
        with patch.object(
            DRILL.VERIFY, "recent_backups", wraps=DRILL.VERIFY.recent_backups
        ):
            from contextlib import redirect_stdout
            from io import StringIO

            stream = StringIO()
            with redirect_stdout(stream):
                exit_code = DRILL.main(
                    [
                        "--backup-dir",
                        str(self.root),
                        "--count",
                        "1",
                        "--jsonl",
                    ]
                )
        self.assertEqual(exit_code, 0)
        payload = json.loads(stream.getvalue())
        self.assertEqual(payload["status"], "pass")
        self.assertEqual(payload["network_calls"], 0)

    def test_missing_backups_fail_without_creation(self):
        """Never fabricate backup copies to satisfy the requested count."""

        with patch.object(DRILL.VERIFY, "recent_backups", return_value=[]):
            self.assertEqual(
                DRILL.main(
                    [
                        "--backup-dir",
                        str(self.root),
                        "--count",
                        "1",
                    ]
                ),
                2,
            )

    def test_argument_validation_is_noninteractive(self):
        """Require safe positive count and maximum archive size."""

        self.assertEqual(
            DRILL.main(
                [
                    "--backup-dir",
                    str(self.root),
                    "--count",
                    "0",
                ]
            ),
            2,
        )
        with self.assertRaises(SystemExit) as raised:
            DRILL.parse_args(["--help"])
        self.assertEqual(raised.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
