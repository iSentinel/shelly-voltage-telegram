"""Unit contracts for independent current-history synthetic A/B restores."""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import sqlite3
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/disposable-current-history-ab-qa.py"
)
SPEC = importlib.util.spec_from_file_location("current_ab_qa", SCRIPT)
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)


class CurrentHistoryABTests(unittest.TestCase):
    """Never use or mutate installed project SQLite files."""

    def setUp(self) -> None:
        """Create a unique 0700 synthetic-root fixture."""
        folder = tempfile.TemporaryDirectory(prefix="current-ab-unit-")
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def database(self, name: str = "synthetic.sqlite3") -> sqlite3.Connection:
        """Make a nontrivial invented SQLite history in WAL mode."""
        conn = sqlite3.connect(self.root / name)
        self.addCleanup(conn.close)
        conn.execute("PRAGMA journal_mode=WAL")
        QA.sqlite_schema(conn)
        QA.populate(conn)
        return conn

    def call_cli(self, *args: str) -> int:
        """Execute only inert or rejected command-line paths."""
        with (
            patch.object(sys, "argv", ["qa", *args]),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            return QA.main()

    def test_default_inert(self) -> None:
        """No flags must not even inspect the guest or touch SQLite."""
        with patch.object(QA, "guest_guard", side_effect=AssertionError):
            self.assertEqual(self.call_cli(), 0)

    def test_exact_consent_required(self) -> None:
        """A generic yes cannot start isolated A/B operations."""
        with patch.object(QA, "guest_guard", side_effect=AssertionError):
            self.assertEqual(
                self.call_cli("--run-disposable", "--confirm", "yes"), 2
            )

    def test_nonroot_guest_rejected(self) -> None:
        """The experimental runner must fail outside permitted root."""
        with (
            patch.object(QA.os, "geteuid", return_value=1000),
            self.assertRaisesRegex(QA.Blocked, "OFFLINE_QA_ONLY"),
        ):
            QA.guest_guard()

    def test_networked_guest_rejected(self) -> None:
        """A network interface beyond loopback prevents execution."""
        with (
            patch.object(QA.os, "geteuid", return_value=0),
            patch.object(
                QA.socket,
                "gethostname",
                return_value="shelly-docker-reboot-qa",
            ),
            patch.object(
                QA.socket,
                "if_nameindex",
                return_value=[(1, "lo"), (2, "eth0")],
            ),
            self.assertRaisesRegex(QA.Blocked, "EXTERNAL_NETWORK_INTERFACE"),
        ):
            QA.guest_guard()

    def test_schema_contains_metadata_and_outages(self) -> None:
        """Synthetic fixture includes more than one measurements table."""
        conn = self.database()
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        self.assertEqual(tables, {"readings", "outages", "meta"})
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 13)

    def test_committed_wal_row_is_present(self) -> None:
        """Backup must preserve data committed in the WAL."""
        conn = self.database()
        QA.append_wal(conn, 257)
        backup = self.root / "backup.sqlite3"
        QA.online_backup(conn, backup)
        with contextlib.closing(sqlite3.connect(backup)) as test:
            count = test.execute("SELECT count(*) FROM readings").fetchone()[0]
        self.assertEqual(count, 257)

    def test_two_independent_current_restores(self) -> None:
        """Both invented A and B must match latest logical history."""
        data = QA.clean_case(self.root)
        self.assertEqual(data["A"], "PASS")
        self.assertEqual(data["B"], "PASS")
        self.assertEqual(data["current_rows"], QA.EXPECTED_ROWS)

    def test_stale_A_after_wal_advance_rejected(self) -> None:
        """A one-row WAL advance makes the old A restore unacceptable."""
        data = QA.stale_case(self.root)
        self.assertEqual(data["A"], "BLOCKED_STALE")
        self.assertEqual(data["B"], "PASS")

    def test_corrupt_backup_header_rejected(self) -> None:
        """Unusable SQLite bytes must not count as restorable."""
        data = QA.corrupted_case(self.root)
        self.assertEqual(data["B"], "BLOCKED_CORRUPT")

    def test_unchanged_row_count_edit_is_detected(self) -> None:
        """Logical checksum must notice an equal-count row mutation."""
        conn = self.database()
        before = QA.canonical(self.root / "synthetic.sqlite3")
        conn.execute("UPDATE readings SET voltage=234.9 WHERE id=42")
        conn.commit()
        self.assertNotEqual(
            before, QA.canonical(self.root / "synthetic.sqlite3")
        )

    def test_sqlite_header_metadata_is_covered(self) -> None:
        """Changing user_version changes the full-history digest."""
        conn = self.database()
        before = QA.canonical(self.root / "synthetic.sqlite3")
        conn.execute("PRAGMA user_version=99")
        conn.commit()
        self.assertNotEqual(
            before, QA.canonical(self.root / "synthetic.sqlite3")
        )

    def test_schema_change_is_covered(self) -> None:
        """An added table cannot preserve the same logical digest."""
        conn = self.database()
        before = QA.canonical(self.root / "synthetic.sqlite3")
        conn.execute("CREATE TABLE new_data(v TEXT)")
        conn.commit()
        self.assertNotEqual(
            before, QA.canonical(self.root / "synthetic.sqlite3")
        )

    def test_source_fixture_sha_tampering_is_blocked(self) -> None:
        """A source blob mismatch invalidates independent restore."""
        conn = self.database()
        digest = QA.canonical(self.root / "synthetic.sqlite3")
        copy = QA.snapshot(conn, self.root, "A")
        self.assertTrue(QA.verify_restore(self.root, copy, digest))
        (copy / "source.fixture").write_bytes(b"changed")
        self.assertFalse(QA.verify_restore(self.root, copy, digest))

    def test_backup_permissions_are_private(self) -> None:
        """Backup SQLite, synthetic source and directory stay private."""
        conn = self.database()
        folder = QA.snapshot(conn, self.root, "A")
        self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o700)
        for filename in ("source.fixture", "current.sqlite3"):
            self.assertEqual(
                stat.S_IMODE((folder / filename).stat().st_mode), 0o600
            )

    def test_repeated_restore_is_stable(self) -> None:
        """A and B independent restore files have identical digests."""
        conn = self.database()
        QA.append_wal(conn, 257)
        digest = QA.canonical(self.root / "synthetic.sqlite3")
        first = QA.snapshot(conn, self.root, "A")
        second = QA.snapshot(conn, self.root, "B")
        self.assertTrue(QA.verify_restore(self.root, first, digest))
        self.assertTrue(QA.verify_restore(self.root, second, digest))
        self.assertEqual(
            QA.canonical(self.root / "restore-A.sqlite3"),
            QA.canonical(self.root / "restore-B.sqlite3"),
        )

    def test_expected_source_checksum_is_deterministic(self) -> None:
        """This fixture is synthetic SHA256 only, never a real signature."""
        digest = hashlib.sha256(QA.SOURCE_FIXTURE).hexdigest()
        folder = self.root / "A"
        folder.mkdir(mode=0o700)
        self.assertEqual(QA.copy_source(self.root, folder), digest)

    def test_runner_reports_three_safe_cases(self) -> None:
        """Suite returns success without mentioning live source paths."""
        result = QA.run_suite()
        self.assertEqual(len(result["cases"]), 3)
        self.assertFalse(result["production_read_or_modified"])
        self.assertEqual(result["signature"], "NOT_TESTED_SYNTHETIC")


if __name__ == "__main__":
    unittest.main()
