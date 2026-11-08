"""Validate inactive YASNO refresh-to-reader handoff service examples."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class YasnoHandoffServiceTests(unittest.TestCase):
    """Do not allow an example to silently affect the production bot."""

    def test_root_handoff_service_is_not_active_unit(self):
        """Root scope is limited to one validated cache copy."""

        path = ROOT / "deploy/systemd/shelly-yasno-handoff.service.example"
        text = path.read_text(encoding="utf-8")
        self.assertTrue(path.name.endswith(".example"))
        for marker in (
            "Type=oneshot",
            "User=root",
            "Group=root",
            "--recipient-gid 10001",
            "ProtectSystem=strict",
            "ReadOnlyPaths=/var/lib/shelly-yasno",
            "ReadWritePaths=/var/lib/shelly-yasno-reader",
            "CapabilityBoundingSet=CAP_CHOWN CAP_DAC_OVERRIDE",
            "NoNewPrivileges=true",
            "ConditionPathExists=/var/lib/shelly-yasno/yasno.json",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, text)
        for marker in ("Telegram", "sendMessage", "/data/voltage.sqlite3"):
            self.assertNotIn(marker, text)

    def test_refresh_triggers_handoff_only_after_success(self):
        """Failed retrieval must not trigger copying stale raw schedules."""

        text = (
            ROOT / "deploy/systemd/shelly-yasno-refresh.service.example"
        ).read_text(encoding="utf-8")
        self.assertIn("OnSuccess=shelly-yasno-handoff.service", text)
        self.assertNotIn("OnFailure=shelly-yasno-handoff.service", text)
        self.assertIn("User=shelly-yasno", text)

    def test_no_implicit_enable_or_root_bot_changes(self):
        """Both units remain examples, while Docker keeps its own UID."""

        names = (
            "shelly-yasno-refresh.service.example",
            "shelly-yasno-handoff.service.example",
            "shelly-yasno-refresh.timer.example",
        )
        for name in names:
            with self.subTest(name=name):
                self.assertTrue((ROOT / "deploy/systemd" / name).exists())
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        self.assertNotIn("user: root", compose)
        self.assertNotIn("privileged: true", compose)
        self.assertNotIn("shelly-yasno-reader", compose)
        self.assertIn("USER 10001:10001", (ROOT / "Dockerfile").read_text())

    def test_docs_require_identity_and_private_recipient(self):
        """User-visible release gates cannot disappear from setup docs."""

        text = (
            (ROOT / "docs/YASNO_SECURE_HANDOFF_QA.md")
            .read_text(encoding="utf-8")
            .casefold()
        )
        for term in ("10001", "private", "approval", "on_success"):
            with self.subTest(term=term):
                self.assertIn(term, text)


if __name__ == "__main__":
    unittest.main()
