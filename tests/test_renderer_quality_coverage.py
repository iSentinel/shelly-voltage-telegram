"""Ensure v5 media-renderer sources are covered by both quality gates."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RENDERERS = (
    "telegram_card_png.py",
    "telegram_card_v5.py",
    "telegram_smooth_type.py",
    "telegram_smooth_bitmap.py",
    "telegram_uk_bitmap.py",
)


class RendererQualityCoverageTests(unittest.TestCase):
    """Prevent newly shipped graphical sources from escaping CI checks."""

    def test_project_checker_includes_every_renderer(self):
        """Check that the custom PEP8/docstring gate scans all renderers."""

        path = ROOT / "scripts/check-quality.py"
        spec = importlib.util.spec_from_file_location("quality_coverage", path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        included = {item.name for item in module.python_files(ROOT)}
        self.assertTrue(set(RENDERERS).issubset(included))

    def test_ruff_format_and_lint_include_every_renderer(self):
        """Check that both Ruff passes inspect the released renderers."""

        source = (ROOT / "scripts/quality.sh").read_text(encoding="utf-8")
        commands = (
            line
            for line in source.splitlines()
            if line.startswith(("ruff format ", "ruff check "))
        )
        checked = list(commands)
        self.assertEqual(len(checked), 2)
        for command in checked:
            with self.subTest(command=command.split()[1]):
                missing = [
                    filename
                    for filename in RENDERERS
                    if filename not in command.split()
                ]
                self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
