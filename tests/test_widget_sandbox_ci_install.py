"""Test native widget layout CI without apt mirror dependency."""

import unittest
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / (
    ".github/workflows/widget-sandbox.yml"
)


class SandboxWorkflowTests(unittest.TestCase):
    """Require Chromium layout checks without OS package installation."""

    def test_browser_install_skips_apt_package_download(self):
        """Install pinned Playwright browser without --with-deps."""
        source = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn(
            "run: corepack pnpm exec playwright install chromium", source
        )
        self.assertNotIn("playwright install --with-deps", source)

    def test_real_renderer_layout_still_runs(self):
        """Do not skip the narrow/wide renderer layout coverage."""
        source = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("Check narrow and wide Telegram bubble layouts", source)
        self.assertIn("SHELLY_WIDGET_HTML=", source)
        self.assertIn(
            "pnpm tsx packages/core/scripts/shelly-widget-layout.ts",
            source,
        )


if __name__ == "__main__":
    unittest.main()
