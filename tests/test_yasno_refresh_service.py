"""QA safety contracts for uninstalled systemd YASNO refresh examples."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class YasnoRefreshServiceTests(unittest.TestCase):
    """Require a restricted, non-activated service example."""

    def test_service_is_uninstalled_example(self):
        """Templates must not be active systemd unit filenames."""

        path = ROOT / "deploy/systemd/shelly-yasno-refresh.service.example"
        text = path.read_text(encoding="utf-8")
        self.assertTrue(path.name.endswith(".example"))
        for required in (
            "User=shelly-yasno",
            "Group=shelly-yasno",
            "StateDirectoryMode=0700",
            "UMask=0077",
            "ProtectSystem=strict",
            "NoNewPrivileges=true",
            "--min-interval-seconds 900",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)
        self.assertNotIn("ExecStartPre=/bin/chmod 777", text)

    def test_timer_is_uninstalled_jittered_example(self):
        """Avoid a startup thundering herd or accidental scheduling."""

        path = ROOT / "deploy/systemd/shelly-yasno-refresh.timer.example"
        text = path.read_text(encoding="utf-8")
        self.assertTrue(path.name.endswith(".example"))
        self.assertIn("OnCalendar=*:0/15", text)
        self.assertIn("RandomizedDelaySec=90s", text)
        self.assertIn("Persistent=true", text)
        self.assertNotIn("OnCalendar=*:0/1", text.splitlines())

    def test_docs_include_dry_run_rollback_and_release_gates(self):
        """Do not publish installer steps without a rollback guide."""

        text = (ROOT / "docs/YASNO_REFRESH_RELEASE_QA.md").read_text(
            encoding="utf-8"
        )
        for required in (
            "--dry-run",
            "--once",
            "--status",
            "disable --now shelly-yasno-refresh.timer",
            "recipient test",
            "bot can read",
            "not installed",
        ):
            with self.subTest(required=required):
                self.assertIn(required.casefold(), text.casefold())


if __name__ == "__main__":
    unittest.main()
