"""Review recovery routes and preserve complete synthetic SQLite evidence."""

import importlib.util
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "recovery_review",
    Path(__file__).resolve().parents[1]
    / "scripts/qa-general-recovery-review.py",
)
assert SPEC and SPEC.loader
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)


class RecoveryReviewTests(unittest.TestCase):
    """Use actual standalone SQLite copies, never the deployed database."""

    def setUp(self):
        """Create equal, independent history and rescue fixtures."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.live = self.root / "live.sqlite3"
        self.rescue = self.root / "rescue.sqlite3"
        with closing(sqlite3.connect(self.live)) as db, db:
            db.execute(
                "CREATE TABLE measurements(id INTEGER PRIMARY KEY, v REAL)"
            )
            db.execute("INSERT INTO measurements VALUES (1,220)")
            db.execute("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)")
            db.execute(
                "INSERT INTO meta VALUES ('daily_summary_day','2026-11-27')"
            )
        with (
            closing(sqlite3.connect(self.live)) as db,
            closing(sqlite3.connect(self.rescue)) as backup,
        ):
            db.backup(backup)

    def review(self, phase, fault, parity=False):
        """Model only the host guard; inspect real fixture bytes."""
        with patch.object(QA, "require_qa"):
            return QA.review(self.root, phase, fault, parity)

    def test_equal_preboot(self):
        """An equal rescue still cannot authorize database restoration."""
        result = self.review("preboot", "general-runtime", True)
        self.assertTrue(result["rescue_matches_live"])
        self.assertEqual(result["route"], "REVIEW_PREBOOT_PREVIOUS_PAIR")
        self.assertFalse(result["restore_database_authorized"])

    def test_postboot_new_history(self):
        """A prior rescue cannot replace post-start measurement history."""
        with closing(sqlite3.connect(self.live)) as db, db:
            db.execute("INSERT INTO measurements VALUES (2,221)")
        before = self.live.read_bytes()
        result = self.review("candidate-may-have-run", "general-runtime")
        self.assertEqual(
            result["route"], "REVIEW_FORWARD_REPAIR_OR_FENCED_HOLD"
        )
        self.assertFalse(result["rescue_matches_live"])
        self.assertEqual(
            (result["live"]["rows"], result["rescue"]["rows"]), (2, 1)
        )
        self.assertFalse(result["rollback_authorized"])
        self.assertEqual(self.live.read_bytes(), before)

    def test_pending_only_change(self):
        """A reservation change matters even without new measurements."""
        with closing(sqlite3.connect(self.live)) as db, db:
            db.execute(
                "INSERT INTO meta VALUES ('telegram_report_pending:x','')"
            )
        result = self.review("preboot", "unknown", True)
        self.assertEqual(
            result["route"], "REVIEW_PREVIOUS_PAIR_UNRESOLVED_DELIVERY"
        )
        self.assertFalse(result["rescue_matches_live"])
        self.assertTrue(result["strict_parity_or_pending_blocker"])

    def test_same_runtime_report_only(self):
        """PAUSED review remains distinct from authorization."""
        result = self.review("verified", "report-only")
        self.assertEqual(result["route"], "REVIEW_SAME_RUNTIME_PAUSED")
        self.assertFalse(result["startup_authorized"])
        self.assertFalse(result["deployment_authorized"])

    def test_unknown_fault(self):
        """Unknown failures must not be classified as report-only."""
        result = self.review("verified", "unknown", True)
        self.assertEqual(
            result["route"], "REVIEW_FORWARD_REPAIR_OR_FENCED_HOLD"
        )

    def test_invalid_inputs(self):
        """Do not accept truthy strings or unrecognized journal phases."""
        for phase, pending in (("invented", False), ("preboot", "false")):
            with self.assertRaisesRegex(ValueError, "invalid recovery"):
                QA.decision(phase, "unknown", pending, True, True)

    def test_sidecar_refused(self):
        """Standalone comparison cannot ignore an existing WAL."""
        self.live.with_name(self.live.name + "-wal").write_bytes(b"")
        with self.assertRaisesRegex(ValueError, "standalone"):
            QA.snapshot(self.live)

    def test_corrupt_database(self):
        """Corruption must not be treated as a recoverable parity result."""
        self.live.write_bytes(b"not SQLite")
        with self.assertRaises(sqlite3.DatabaseError):
            QA.snapshot(self.live)

    def test_ordinary_path_required(self):
        """Reject symlink aliases before opening SQLite."""
        alias = self.root / "alias.sqlite3"
        alias.symlink_to(self.live)
        with self.assertRaisesRegex(ValueError, "unsafe evidence"):
            QA.snapshot(alias)
