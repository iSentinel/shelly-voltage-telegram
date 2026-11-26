"""Exercise exact report reconciliation on disposable SQLite history."""

import argparse
import importlib.util
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from writer_fence import WriterFenceError, writer_lease

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/reconcile-report-delivery.py"
)
SPEC = importlib.util.spec_from_file_location("report_repair", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
REPAIR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPAIR)


class ReportRepairTests(unittest.TestCase):
    """Only modify a synthetic database, never a live installation."""

    def setUp(self):
        """Prepare a pending report and unrelated persisted measurements."""
        scope = tempfile.TemporaryDirectory()
        self.addCleanup(scope.cleanup)
        self.path = Path(scope.name) / "monitor.sqlite3"
        self.backup = self.path.with_name("backup.sqlite3")
        self.key = REPAIR.DAILY
        self.period = "2026-11-26"
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executescript(
                "PRAGMA journal_mode=WAL;"
                "CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);"
                "CREATE TABLE measurements(voltage REAL);"
                "INSERT INTO measurements VALUES(220);"
            )
            db.execute(
                "INSERT INTO meta VALUES(?,?)",
                (REPAIR.PREFIX + self.key, self.period),
            )

    def args(self, outcome="delivered"):
        """Bind the explicit operator decision to current reviewed state."""
        return argparse.Namespace(
            database=self.path,
            stream=self.key,
            backup=self.backup,
            expect_fingerprint=REPAIR.inspect(self.path, self.key)[
                "fingerprint"
            ],
            outcome=outcome,
            evidence_ref="qa-simulated-remote",
            message_id="42" if outcome == "delivered" else None,
        )

    def apply(self, args):
        """Substitute only the host-specific QA boundary."""
        with patch.object(REPAIR, "require_qa"):
            return REPAIR.apply(args)

    def metadata(self, path=None):
        """Read current metadata and verify unrelated history."""
        with closing(sqlite3.connect(path or self.path)) as db:
            self.assertEqual(
                db.execute("SELECT voltage FROM measurements").fetchall(),
                [(220.0,)],
            )
            return dict(db.execute("SELECT key,value FROM meta"))

    def test_delivered_preserves_history_and_rescue(self):
        """Commit the exact period and audit; keep the pending backup."""
        inode = self.path.stat().st_ino
        result = self.apply(self.args())
        meta = self.metadata()
        self.assertEqual(meta[self.key], self.period)
        self.assertNotIn(REPAIR.PREFIX + self.key, meta)
        self.assertEqual(
            self.metadata(self.backup)[REPAIR.PREFIX + self.key], self.period
        )
        self.assertEqual(self.path.stat().st_ino, inode)
        self.assertEqual(
            sum(k.startswith("telegram_report_resolution:") for k in meta), 1
        )
        self.assertFalse(result["deployment_authorized"])

    def test_rejected_is_not_marked_delivered(self):
        """A confirmed rejection clears only its exact reservation."""
        self.apply(self.args("rejected"))
        self.assertNotIn(self.key, self.metadata())
        self.assertNotIn(REPAIR.PREFIX + self.key, self.metadata())

    def test_stale_fingerprint_refuses_write(self):
        """A changed review fingerprint must not create a rescue or mutate."""
        args = self.args()
        args.expect_fingerprint = "0" * 64
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            self.apply(args)
        self.assertFalse(self.backup.exists())
        self.assertIn(REPAIR.PREFIX + self.key, self.metadata())

    def test_active_writer_refuses_reconciliation(self):
        """Do not acquire a second writer lease."""
        args = self.args()
        with writer_lease(self.path), self.assertRaises(WriterFenceError):
            self.apply(args)
        self.assertFalse(self.backup.exists())

    def test_newer_delivered_period_refused(self):
        """Never regress a delivered period while resolving old state."""
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                "INSERT INTO meta VALUES(?,?)", (self.key, "2026-11-27")
            )
        with self.assertRaisesRegex(ValueError, "conflicts"):
            REPAIR.inspect(self.path, self.key)

    def test_bad_period_or_stream(self):
        """Reject ambiguous dates, invalid ISO weeks and unrelated keys."""
        for key, period in (
            (self.key, "2026-02-30"),
            (self.key, "20261126"),
            ("unrelated", "2026-11-26"),
            ("telegram_weekly_private_sent_7", "2026-W54"),
        ):
            self.assertFalse(REPAIR.valid_period(key, period))
        self.assertTrue(
            REPAIR.valid_period("telegram_weekly_private_sent_7", "2026-W48")
        )

    def test_existing_backup_refuses_write(self):
        """Never overwrite an existing rescue archive."""
        args = self.args()
        self.backup.write_bytes(b"existing")
        with self.assertRaises(FileExistsError):
            self.apply(args)
        self.assertEqual(self.backup.read_bytes(), b"existing")
        self.assertIn(REPAIR.PREFIX + self.key, self.metadata())

    def test_qa_boundary_rejects_other_host(self):
        """Production apply is not enabled by this QA helper."""
        with (
            patch.object(
                REPAIR.socket, "gethostname", return_value="production"
            ),
            self.assertRaisesRegex(ValueError, "isolated QA"),
        ):
            REPAIR.require_qa(self.path, self.backup)

    def test_symlink_database_refused(self):
        """Do not inspect or mutate a database through a link."""
        link = self.path.with_name("link.sqlite3")
        link.symlink_to(self.path)
        with self.assertRaisesRegex(ValueError, "link-free"):
            REPAIR.inspect(link, self.key)
