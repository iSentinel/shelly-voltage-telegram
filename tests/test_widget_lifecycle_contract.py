"""Static acceptance contracts for Telegram live-data lifecycle QA."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "tests" / "widget-lifecycle-scenarios.ts"
WORKFLOW = ROOT / ".github" / "workflows" / "widget-sandbox.yml"


class WidgetLifecycleContractTests(unittest.TestCase):
    """Keep real QuickJS update acceptance as part of the CI gate."""

    def test_workflow_runs_lifecycle_in_pinned_sandbox(self):
        """The updated script must run after official widget sandbox."""

        action = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("widget-lifecycle-scenarios.ts", action)
        self.assertIn("shelly-widget-lifecycle.ts", action)
        self.assertIn("SHELLY_WIDGET_HTML", action)
        self.assertIn("3acba1b62093c83815d38603d26f48c4f26a94c2", action)

    def test_transitions_cover_stale_and_recovery(self):
        """A single host should apply five state versions and probe each."""

        script = SCENARIOS.read_text(encoding="utf-8")
        for expected in (
            "offline Russian",
            "recovered Ukrainian",
            "expired schedule",
            "one phase again",
            "result.probes.length, 5",
            "result.watchesPublicData",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, script)

    def test_stress_checks_dom_limits_and_final_state(self):
        """Exercise many real QuickJS updates without accumulating nodes."""

        script = (ROOT / "tests" / "widget-update-stress.ts").read_text(
            encoding="utf-8"
        )
        action = WORKFLOW.read_text(encoding="utf-8")
        for marker in (
            "widget-update-stress.ts",
            "shelly-widget-stress.ts",
        ):
            self.assertIn(marker, action)
        for marker in (
            "result.nodes < 2000",
            "result.stopped",
            "result.requests.length, 0",
            "34 updates",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, script)

    def test_no_network_or_telegram_send(self):
        """Keep the lifecycle acceptance isolated from live Bot API."""

        script = SCENARIOS.read_text(encoding="utf-8")
        self.assertIn("result.requests.length, 0", script)
        for forbidden in ("fetch(", "sendWidget(", "axios(", "http.request("):
            self.assertNotIn(forbidden, script)


if __name__ == "__main__":
    unittest.main()
