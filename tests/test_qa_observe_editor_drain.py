"""Test selected editor retirement policy, not actual Telegram delivery."""

import argparse
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from cutover_journal import JournalBlocked  # noqa: E402
from qa_observe_editor_drain import (  # noqa: E402
    check_container,
    check_unit,
    observe,
)

ID = "a" * 64
TIMER = {
    "LoadState": "masked",
    "ActiveState": "inactive",
    "SubState": "dead",
    "UnitFileState": "masked",
    "NeedDaemonReload": "no",
    "mask": True,
    "Job": "",
}
SERVICE = dict(TIMER, MainPID="0", ControlPID="0")
BOX = {
    "id": ID,
    "running": False,
    "restarting": False,
    "state": "exited",
    "restart": "no",
    "network": "none",
}


class DrainTests(unittest.TestCase):
    """Check both launch paths and the old container must be retired."""

    def test_retired(self):
        """Both units and stopped immutable old container pass."""
        check_unit(TIMER, False)
        check_unit(SERVICE, True)
        check_container(BOX, ID)

    def test_timer_unsafe(self):
        """Disabled alone is not a durable mask or a drained timer."""
        for key, value in (
            ("UnitFileState", "disabled"),
            ("mask", False),
            ("ActiveState", "active"),
            ("Job", "31"),
            ("NeedDaemonReload", "yes"),
            ("LoadState", "loaded"),
        ):
            with self.subTest(key=key), self.assertRaises(JournalBlocked):
                check_unit(dict(TIMER, **{key: value}), False)

    def test_service_unsafe(self):
        """A live PID, queued start or in-flight edit remains blocked."""
        for key, value in (
            ("MainPID", "2345"),
            ("ControlPID", "99"),
            ("ActiveState", "activating"),
            ("Job", "17"),
        ):
            with self.subTest(key=key), self.assertRaises(JournalBlocked):
                check_unit(dict(SERVICE, **{key: value}), True)

    def test_container_unsafe(self):
        """Reject a running/recreated container or independent restart."""
        for key, value in (
            ("id", "b" * 64),
            ("running", True),
            ("restarting", True),
            ("restart", "always"),
            ("state", "paused"),
        ):
            with self.subTest(key=key), self.assertRaises(JournalBlocked):
                check_container(dict(BOX, **{key: value}), ID)

    def test_drift(self):
        """Observe twice and keep unknown remote outcomes unresolved."""
        args = argparse.Namespace(
            qa_only=True,
            editor="svt-qa-editor",
            container="svt-qa-old",
            identity=ID,
        )
        with (
            patch("qa_observe_editor_drain.os.geteuid", return_value=0),
            patch(
                "qa_observe_editor_drain.socket.gethostname",
                return_value="shelly-docker-reboot-qa",
            ),
            patch(
                "qa_observe_editor_drain.fields",
                side_effect=[TIMER, SERVICE, TIMER, SERVICE],
            ),
            patch("qa_observe_editor_drain.container_state", return_value=BOX),
        ):
            report = observe(args)
        self.assertFalse(report["remote_delivery_reconciled"])
        self.assertFalse(report["release_authorized"])
        self.assertFalse(report["all_editors_verified"])


if __name__ == "__main__":
    unittest.main()
