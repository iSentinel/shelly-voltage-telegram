"""Review final launch identity and actual retained probe fingerprints."""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_qa_durable_report_start import QA


class ProbeTests(unittest.TestCase):
    """Use the exact retained fixture files, not invented probe bytes."""

    def setUp(self):
        """Prepare disposable files; model only root ownership."""
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        probe = self.root / "probe"
        probe.mkdir()
        tests = Path(__file__).resolve().parent
        shutil.copyfile(
            tests / "fixtures/report-worker/probe.py.txt", probe / "probe.py"
        )
        shutil.copyfile(tests / "test_app.py", probe / "test_app.py")
        self.addCleanup(patch.stopall)
        patch.object(QA.TX, "protected").start()

    def test_exact_probe(self):
        """The actual two retained files match their configured hashes."""
        QA.check_probe(self.root)

    def test_probe_drift(self):
        """A single changed byte invalidates the expected probe."""
        path = self.root / "probe/probe.py"
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "probe bytes changed"):
            QA.check_probe(self.root)

    def test_extra_import(self):
        """Refuse any additional probe-directory file."""
        (self.root / "probe/extra.py").touch()
        with self.assertRaisesRegex(ValueError, "unexpected probe"):
            QA.check_probe(self.root)


class FinalObservationTests(unittest.TestCase):
    """Model the Docker boundary while checking observation ordering."""

    def setUp(self):
        """Prepare a fixed running launch and its protected evidence."""
        self.root = Path("/var/lib/svt-report-mode-tx-qa.synthetic")
        self.info = {
            "Id": "a" * 64,
            "RestartCount": 0,
            "State": {"Running": True, "StartedAt": "2026-11-27T12:00:00Z"},
        }
        self.journal = {"baseline": {"rows": 12}}
        self.addCleanup(patch.stopall)
        patch.object(
            QA,
            "launch_evidence",
            return_value=({"container_id": self.info["Id"]}, self.journal),
        ).start()
        self.inspect = patch.object(
            QA.TX, "inspect_qa_container", return_value=self.info
        ).start()
        self.launch = patch.object(QA.RECEIPT, "check_launch").start()
        self.observed = patch.object(QA, "check_observed").start()
        self.ready = patch.object(QA.READY, "fetch_ready").start()
        patch.object(QA.READY, "live_phase_evidence", return_value={}).start()
        self.history = patch.object(
            QA.TX, "history_snapshot", return_value={"rows": 30}
        ).start()
        self.persist = patch.object(QA, "persist_acceptance").start()

    def observe(self):
        """Invoke the real QA observation flow without Docker."""
        return QA.observe(self.root, "123456abcdef", "b" * 64)

    def test_history_after_readiness(self):
        """Persist only after fresh readiness and final evidence checks."""

        def after_ready(*args):
            """Check observation ordering at the history capture."""
            self.ready.assert_called_once()
            self.assertEqual(self.launch.call_count, 2)
            self.assertEqual(self.observed.call_count, 2)
            return {"rows": 30}

        self.history.side_effect = after_ready
        self.observe()
        self.persist.assert_called_once()

    def test_final_restart_refused(self):
        """A final restart-count change cannot reach acceptance."""
        self.launch.side_effect = [None, ValueError("launch receipt mismatch")]
        with self.assertRaisesRegex(ValueError, "launch receipt mismatch"):
            self.observe()
        self.persist.assert_not_called()

    def test_final_profile_drift_refused(self):
        """A final historical or runtime drift cannot reach acceptance."""
        self.observed.side_effect = [
            None,
            ValueError("runtime identity drift"),
        ]
        with self.assertRaisesRegex(ValueError, "identity drift"):
            self.observe()
        self.persist.assert_not_called()
