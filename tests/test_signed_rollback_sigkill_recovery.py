"""Verify durable disposable rollback rescue after SIGKILL of a child."""

from __future__ import annotations

import importlib.util
import json
import signal
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/signed-rollback-disposable-rehearsal.py"
SPEC = importlib.util.spec_from_file_location("sigkill_recovery", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)
OLD = "0.5.24-hw"
TARGET = "0.5.25-hw"
DIGEST = "sha256:" + "a" * 64

CHILD = """
import importlib.util, os, signal, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location('child_rehearsal', sys.argv[1])
qa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qa)
folder, signed, fault = Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4]
def real_kill(path, phase, expected):
    qa.journal(path, phase)
    if phase == expected:
        os.kill(os.getpid(), signal.SIGKILL)
qa.fail_at = real_kill
qa.run_case(folder, signed, '0.5.24-hw', '0.5.25-hw', fault)
"""


class ProcessKillRollbackTests(unittest.TestCase):
    """Kill only isolated Python children, then recover local test SQLite."""

    def setUp(self) -> None:
        """Prepare disposable signed metadata and an isolated work folder."""

        self.temporary = tempfile.TemporaryDirectory(prefix="svt-kill-qa-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.signed = self.root / "signed"
        self.signed.mkdir(mode=0o700)
        receipt = self.signed / "deployed.json"
        receipt.write_text(json.dumps({"version": OLD, "digest": DIGEST}))
        receipt.chmod(0o600)
        override = self.signed / "override.yaml"
        override.write_text(
            "services:\n  shelly-voltage:\n"
            "    image: ghcr.io/isentinel/shelly-voltage-telegram@"
            + DIGEST
            + "\n"
        )
        override.chmod(0o600)

    def kill_disposable_child(self, fault: str) -> Path:
        """Run one synthetic transaction and require an actual SIGKILL."""

        folder = self.root / fault
        folder.mkdir(mode=0o700)
        args = [
            sys.executable,
            "-c",
            CHILD,
            str(SCRIPT),
            str(folder),
            str(self.signed),
            fault,
        ]
        process = subprocess.run(
            args, capture_output=True, text=True, timeout=15, check=False
        )
        self.assertEqual(process.returncode, -signal.SIGKILL)
        return folder

    def test_actual_sigkill_then_recovers_each_synthetic_boundary(self):
        """Persistent rescue must restore newest data and PHOTO status."""

        for fault in QA.FAULTS:
            with self.subTest(fault=fault):
                folder = self.kill_disposable_child(fault)
                journal = json.loads(
                    (folder / "journal.json").read_text(encoding="ascii")
                )
                self.assertEqual(journal["phase"], fault)
                newest = QA.fixture_digest(folder / "rescue.sqlite3")
                self.assertEqual(newest[1], 10)
                QA.recover_fenced(folder, newest[0], TARGET)
                self.assertEqual(
                    QA.fixture_digest(folder / "active.sqlite3"), newest
                )
                self.assertEqual(QA.staged_identity(folder)[0], TARGET)
                state = json.loads(
                    (folder / "journal.json").read_text(encoding="ascii")
                )
                self.assertEqual(state["phase"], "RECOVERED_NO_BOT_START")

    def test_missing_rescue_after_kill_blocks_recovery(self):
        """No saved SQLite means no claim of safe automated recovery."""

        folder = self.kill_disposable_child("after_old_db_swap")
        (folder / "rescue.sqlite3").unlink()
        with self.assertRaisesRegex(QA.Blocked, "RESCUE_HISTORY_MISMATCH"):
            QA.recover_fenced(folder, "0" * 64, TARGET)
        self.assertEqual(QA.fixture_digest(folder / "active.sqlite3")[1], 6)

    def test_contending_writer_blocks_post_kill_recovery(self):
        """Recovery also requires an uncontended file lock."""

        import fcntl

        folder = self.kill_disposable_child("after_old_db_swap")
        expected = QA.fixture_digest(folder / "rescue.sqlite3")[0]
        with (folder / "synthetic-writer.lock").open("r+") as writer:
            fcntl.flock(writer, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(QA.Blocked, "WRITER_NOT_FENCED"):
                QA.recover_fenced(folder, expected, TARGET)
        self.assertEqual(QA.fixture_digest(folder / "active.sqlite3")[1], 6)
        QA.recover_fenced(folder, expected, TARGET)
        self.assertEqual(QA.fixture_digest(folder / "active.sqlite3")[1], 10)

    def test_photo_record_survives_real_child_kill(self):
        """Even after a killed child, exact mock PHOTO ID survives."""

        folder = self.kill_disposable_child("after_old_db_swap")
        original = QA.fixture_digest(folder / "rescue.sqlite3")[0]
        QA.recover_fenced(folder, original, TARGET)
        with closing(sqlite3.connect(folder / "active.sqlite3")) as connection:
            marker = connection.execute(
                "SELECT media_kind, message_id FROM telegram_status"
            ).fetchone()
        self.assertEqual(marker, ("photo", 1000))


if __name__ == "__main__":
    unittest.main()
