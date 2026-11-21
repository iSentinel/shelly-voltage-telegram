"""Synthetic tests for a read-only disposable legacy owner observation."""

import argparse
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from cutover_journal import JournalBlocked  # noqa: E402
from qa_observe_legacy_stop import check_inhibition, observe  # noqa: E402

IMAGE = "sha256:" + "a" * 64
UNIT = {
    "persistent_mask": True,
    "LoadState": "masked",
    "UnitFileState": "masked",
    "NeedDaemonReload": "no",
    "Job": "",
    "ActiveState": "inactive",
}
BOX = {
    "id": "b" * 64,
    "image": IMAGE,
    "restart": "no",
    "running": False,
    "restarting": False,
    "state": "exited",
    "network": "none",
}


class LegacyObservationTests(unittest.TestCase):
    """Check only the named owners; never infer sole writer ownership."""

    def setUp(self):
        """Use synthetic data for explicitly isolated QA targets."""
        self.args = argparse.Namespace(
            qa_only=True,
            unit="svt-qa-legacy-test.service",
            container="svt-qa-legacy-test",
            image=IMAGE,
            require_stopped=True,
        )

    def evaluate(self, unit=None, box=None):
        """Mock only live reads, preserving the real decision logic."""
        with (
            patch(
                "qa_observe_legacy_stop.query",
                return_value="shelly-docker-reboot-qa",
            ),
            patch(
                "qa_observe_legacy_stop.unit_state", return_value=unit or UNIT
            ),
            patch(
                "qa_observe_legacy_stop.container_state",
                return_value=box or BOX,
            ),
        ):
            return observe(self.args)

    def test_stopped(self):
        """A stopped exact container with persistent mask passes."""
        report = self.evaluate()
        self.assertTrue(report["selected_container_stopped"])
        self.assertFalse(report["all_writers_verified"])
        self.assertFalse(report["release_authorized"])

    def test_inhibited_running_not_stopped(self):
        """Restart inhibition alone never proves a stopped writer."""
        unit = dict(UNIT, ActiveState="active")
        box = dict(BOX, running=True, state="running")
        with self.assertRaises(JournalBlocked):
            self.evaluate(unit, box)
        self.args.require_stopped = False
        self.assertFalse(
            self.evaluate(unit, box)["selected_container_stopped"]
        )

    def test_mask_drift_refused(self):
        """Reject volatile masks, active jobs and stale manager state."""
        for key, value in (
            ("persistent_mask", False),
            ("LoadState", "loaded"),
            ("UnitFileState", "masked-runtime"),
            ("Job", "42"),
            ("NeedDaemonReload", "yes"),
            ("ActiveState", "activating"),
        ):
            with self.subTest(key=key), self.assertRaises(JournalBlocked):
                check_inhibition(dict(UNIT, **{key: value}), BOX, IMAGE)

    def test_container_drift_refused(self):
        """Reject wrong image, restart policies and enabled network."""
        for key, value in (
            ("image", "sha256:" + "d" * 64),
            ("restart", "unless-stopped"),
            ("restarting", True),
            ("network", "bridge"),
            ("running", "false"),
            ("id", "short"),
        ):
            with self.subTest(key=key), self.assertRaises(JournalBlocked):
                check_inhibition(UNIT, dict(BOX, **{key: value}), IMAGE)

    def test_live_read_changes_refused(self):
        """Two observations must agree before accepting the evidence."""
        with (
            patch(
                "qa_observe_legacy_stop.query",
                return_value="shelly-docker-reboot-qa",
            ),
            patch("qa_observe_legacy_stop.unit_state", return_value=UNIT),
            patch(
                "qa_observe_legacy_stop.container_state",
                side_effect=[BOX, dict(BOX, id="c" * 64)],
            ),
            self.assertRaises(JournalBlocked),
        ):
            observe(self.args)

    def test_nonqa_name_refused(self):
        """Production names are not accepted."""
        self.args.container = "shelly-voltage-bot"
        with self.assertRaises(JournalBlocked):
            self.evaluate()


if __name__ == "__main__":
    unittest.main()
