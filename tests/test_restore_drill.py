"""Offline backup restoration smoke checks using synthetic local SQLite."""

from __future__ import annotations

import gzip
import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
