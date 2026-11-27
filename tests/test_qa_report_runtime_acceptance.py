"""Exercise three-phase acceptance without production or Docker access."""

import importlib.util
import sqlite3
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "acceptance",
    Path(__file__).resolve().parents[1]
    / "scripts/qa-report-runtime-acceptance.py",
)
assert SPEC and SPEC.loader
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)


class RuntimeAcceptanceTests(unittest.TestCase):
    """Verify fail-closed readiness and post-start history rules."""

    def setUp(self):
        """Create three fresh synthetic samples per phase."""
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.execute(
            "CREATE TABLE measurements(id INTEGER PRIMARY KEY,ts REAL,"
            "voltage REAL,frequency REAL,phase TEXT)"
        )
        self.db.executemany(
            "INSERT INTO measurements(ts,voltage,frequency,phase) "
            "VALUES(?,220,50,?)",
            [(ts, phase) for ts in (91, 93, 95) for phase in QA.PHASES],
        )
        self.payload = {
            "ready": True,
            "unready_phases": [],
            "phases": {
                p: {"ready": True, "connectivity": "online", "age": 1}
                for p in QA.PHASES
            },
        }

    def evidence(self):
        """Review samples after the synthetic start at second 90."""
        return QA.phase_evidence(self.db, 0, 100, 90)

    def test_fresh_complete_phases(self):
        """Require all three phases with three samples each."""
        QA.validate_ready(self.payload)
        self.assertEqual(set(self.evidence()), set(QA.PHASES))

    def test_missing_phase_readiness(self):
        """Single-phase or missing L3 readiness is refused."""
        del self.payload["phases"]["L3"]
        with self.assertRaisesRegex(ValueError, "three-phase"):
            QA.validate_ready(self.payload)

    def test_stale_or_invalid_age(self):
        """Reject stale, boolean, infinite and negative ages."""
        for age in (11, True, float("inf"), -1):
            self.payload["phases"]["L2"]["age"] = age
            with self.assertRaisesRegex(ValueError, "stale"):
                QA.validate_ready(self.payload)

    def test_missing_samples(self):
        """Readiness alone cannot replace persisted phase evidence."""
        self.db.execute("DELETE FROM measurements WHERE phase='L3'")
        with self.assertRaisesRegex(ValueError, "insufficient"):
            self.evidence()

    def test_invalid_voltage(self):
        """A zero or unsafe voltage cannot certify this fixture."""
        self.db.execute("UPDATE measurements SET voltage=0 WHERE phase='L2'")
        with self.assertRaisesRegex(ValueError, "invalid or stale"):
            self.evidence()

    def test_prestart_samples(self):
        """Old rows cannot count as new runtime evidence."""
        with self.assertRaisesRegex(ValueError, "invalid or stale"):
            QA.phase_evidence(self.db, 0, 100, 92)

    def test_duplicate_timestamps(self):
        """Repeated timestamps are not three independent observations."""
        self.db.execute("UPDATE measurements SET ts=95 WHERE phase='L1'")
        with self.assertRaisesRegex(ValueError, "nonmonotonic"):
            self.evidence()
