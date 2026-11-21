"""Test QA journal durability and refusal policies offline."""

import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from cutover_journal import STAGES, Journal, JournalBlocked  # noqa: E402

IDENTITY = {
    "version": "0.5.26-hw",
    "commit": "a" * 40,
    "source_sha256": "b" * 64,
    "oci_digest": "sha256:" + "c" * 64,
    "token": "qa-transition-1",
}
PROOF = hashlib.sha256(b"synthetic observation").hexdigest()


class CutoverJournalTests(unittest.TestCase):
    """No simulated proof grants release authorization."""

    def setUp(self):
        """Initialize one private disposable journal per test."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.journal = Journal(self.root, IDENTITY)
        self.journal.initialize()

    def test_intent_requires_reconciliation(self):
        """Restart after intent never suggests repeating the action."""
        self.journal.append("intent", STAGES[0])
        reopened = Journal(self.root, IDENTITY)
        self.assertEqual(reopened.inspect()["decision"], "RECONCILE_REQUIRED")
        with self.assertRaises(JournalBlocked):
            reopened.append("intent", STAGES[0])
        reopened.append("observe", STAGES[0], PROOF)
        self.assertEqual(reopened.inspect()["stage"], STAGES[1])

    def test_complete_is_not_authorization(self):
        """All records complete still does not authorize installation."""
        for stage in STAGES:
            self.journal.append("intent", stage)
            self.journal.append("observe", stage, PROOF)
        result = self.journal.inspect()
        self.assertEqual(result["decision"], "RECORDS_COMPLETE_ONLY")
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["production_modified"])
        with self.assertRaises(JournalBlocked):
            self.journal.append("intent", STAGES[0])

    def test_out_of_order(self):
        """Reject skipped stages or observations without an intent."""
        for kind, stage in (("intent", STAGES[1]), ("observe", STAGES[0])):
            with self.assertRaises(JournalBlocked):
                self.journal.append(
                    kind, stage, PROOF if kind == "observe" else ""
                )
        self.assertEqual(self.journal.inspect()["records"], 0)

    def test_identity_drift(self):
        """A different signed candidate cannot resume this journal."""
        changed = dict(IDENTITY, commit="d" * 40)
        with self.assertRaises(JournalBlocked):
            Journal(self.root, changed).inspect()

    def test_missing_evidence(self):
        """Reject missing or malformed observation digests."""
        self.journal.append("intent", STAGES[0])
        for proof in ("", "not-a-hash"):
            with self.assertRaises(JournalBlocked):
                self.journal.append("observe", STAGES[0], proof)
        self.assertEqual(self.journal.inspect()["records"], 1)

    def test_corrupt_state(self):
        """Detect accidental state modification before any next action."""
        state = json.loads(self.journal.path.read_text())
        state["events"].append({"kind": "intent"})
        self.journal.path.write_text(json.dumps(state))
        with self.assertRaises(JournalBlocked):
            self.journal.inspect()

    def test_failed_replace_preserves_previous_state(self):
        """A crash/error before the atomic replace leaves old intent."""
        self.journal.append("intent", STAGES[0])
        with (
            patch("cutover_journal.os.replace", side_effect=OSError("fault")),
            self.assertRaises(OSError),
        ):
            self.journal.append("observe", STAGES[0], PROOF)
        self.assertEqual(
            self.journal.inspect()["decision"], "RECONCILE_REQUIRED"
        )
        self.assertFalse(list(self.root.glob(".state-*")))

    def test_process_sigkill_after_intent(self):
        """A real killed process leaves a durable, reconcilable intent."""
        code = (
            "import json,os,signal,sys;from pathlib import Path;"
            "from cutover_journal import Journal,STAGES;"
            "j=Journal(Path(sys.argv[1]),json.loads(sys.argv[2]));"
            "j.append('intent',STAGES[0]);"
            "os.kill(os.getpid(),signal.SIGKILL)"
        )
        env = dict(
            os.environ,
            PYTHONPATH=str(Path(__file__).resolve().parents[1] / "scripts"),
        )
        child = subprocess.run(
            [
                sys.executable,
                "-B",
                "-c",
                code,
                str(self.root),
                json.dumps(IDENTITY),
            ],
            env=env,
            check=False,
            timeout=10,
        )
        self.assertEqual(child.returncode, -signal.SIGKILL)
        self.assertEqual(
            self.journal.inspect()["decision"], "RECONCILE_REQUIRED"
        )
        self.journal.append("observe", STAGES[0], PROOF)
        self.assertEqual(self.journal.inspect()["stage"], STAGES[1])

    def test_no_overwrite_or_symlink(self):
        """Reject reinitialization and symlink state files."""
        with self.assertRaises(JournalBlocked):
            self.journal.initialize()
        self.journal.path.unlink()
        self.journal.path.symlink_to(self.root / "missing")
        with self.assertRaises(OSError):
            self.journal.inspect()
        with self.assertRaises(JournalBlocked):
            self.journal.initialize()

    def test_private_storage(self):
        """Reject a state file with unsafe mode or hardlink."""
        os.chmod(self.journal.path, 0o644)
        with self.assertRaises(JournalBlocked):
            self.journal.inspect()
        os.chmod(self.journal.path, 0o600)
        os.link(self.journal.path, self.root / "hardlink")
        with self.assertRaises(JournalBlocked):
            self.journal.inspect()

    def test_concurrent_writer_refused(self):
        """A second updater cannot append while a journal lock is held."""
        with self.journal.locked(), self.assertRaises(JournalBlocked):
            Journal(self.root, IDENTITY).append("intent", STAGES[0])


if __name__ == "__main__":
    unittest.main()
