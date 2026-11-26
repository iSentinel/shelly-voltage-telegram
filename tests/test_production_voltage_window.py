"""Exercise the read-only three-phase production voltage window."""

import importlib.util
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from scripts import github_update

PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "production-voltage-window.py"
)
SPEC = importlib.util.spec_from_file_location("voltage_window", PATH)
assert SPEC and SPEC.loader
WINDOW = importlib.util.module_from_spec(SPEC)
with patch.dict("sys.modules", {"github_update": github_update}):
    SPEC.loader.exec_module(WINDOW)

NOW = 10000.0


class VoltageWindowTests(unittest.TestCase):
    """Use actual SQLite fixtures, with no network or production writes."""

    def setUp(self):
        """Build a complete three-phase 10-second sample window."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "voltage.sqlite3"
        with closing(sqlite3.connect(self.db)) as db, db:
            db.executescript(
                "CREATE TABLE measurements(phase,ts,voltage,frequency);"
                "CREATE TABLE outages(ended_at);"
            )
            db.executemany(
                "INSERT INTO measurements VALUES(?,?,?,?)",
                [
                    (p, NOW - 900 + i * 10, 220.0, 50.0)
                    for p in WINDOW.PHASES
                    for i in range(91)
                ],
            )

    def mutate(self, sql):
        """Change only a synthetic test fixture."""
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute(sql)

    def report(self):
        """Read a window at the fixed fixture clock."""
        return WINDOW.window_report(self.db, NOW)

    def test_valid_window(self):
        """Accept complete, uniformly safe coverage for all phases."""
        self.assertEqual(self.report()["reason_codes"], [])

    def test_one_phase_low_voltage(self):
        """Reject an earlier low value even when the latest sample is safe."""
        self.mutate(
            "UPDATE measurements SET voltage=201.2 "
            "WHERE phase='L3' AND ts=9500"
        )
        self.assertIn(
            "VOLTAGE_OUTSIDE_BAND",
            self.report()["phases"]["L3"]["reason_codes"],
        )

    def test_interior_gap(self):
        """Reject a gap even if enough total observations remain."""
        self.mutate(
            "DELETE FROM measurements WHERE phase='L2' "
            "AND ts BETWEEN 9400 AND 9450"
        )
        self.assertIn(
            "SAMPLE_GAP_OR_ORDER",
            self.report()["phases"]["L2"]["reason_codes"],
        )

    def test_missing_phase(self):
        """Do not silently accept single- or two-phase data."""
        self.mutate("DELETE FROM measurements WHERE phase='L3'")
        self.assertIn("PHASE_WINDOW_UNSAFE", self.report()["reason_codes"])

    def test_recently_closed_outage(self):
        """Reject a recovery that intersects the observation window."""
        self.mutate("INSERT INTO outages VALUES(9900)")
        self.assertIn(
            "OUTAGE_INTERSECTS_WINDOW", self.report()["reason_codes"]
        )

    def test_open_outage(self):
        """Reject an unresolved incident."""
        self.mutate("INSERT INTO outages VALUES(NULL)")
        self.assertIn(
            "OUTAGE_INTERSECTS_WINDOW", self.report()["reason_codes"]
        )

    def test_invalid_frequency(self):
        """Reject invalid physical readings."""
        self.mutate("UPDATE measurements SET frequency=0 WHERE ts=9500")
        self.assertIn(
            "INVALID_FREQUENCY",
            self.report()["phases"]["L1"]["reason_codes"],
        )

    def test_duplicate_timestamp(self):
        """Reject duplicate sampling timestamps within one phase."""
        self.mutate("INSERT INTO measurements VALUES('L1',9500,220,50)")
        self.assertIn(
            "SAMPLE_GAP_OR_ORDER",
            self.report()["phases"]["L1"]["reason_codes"],
        )

    def test_stock_preflight_remains_required(self):
        """No window can override the stock release protections."""
        with (
            patch.object(WINDOW.time, "time", return_value=NOW),
            patch.object(WINDOW, "readiness"),
            patch.object(
                WINDOW.github_update, "preflight", side_effect=ValueError
            ),
        ):
            result = WINDOW.audit(self.db)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["deployment_authorized"])

    def test_pass_never_authorizes_deployment(self):
        """Even complete evidence remains a read-only review."""
        with (
            patch.object(WINDOW.time, "time", return_value=NOW),
            patch.object(WINDOW, "readiness"),
            patch.object(WINDOW.github_update, "preflight"),
        ):
            result = WINDOW.audit(self.db)
        self.assertEqual(result["status"], "PASS_WINDOW_ONLY")
        self.assertFalse(result["deployment_authorized"])
