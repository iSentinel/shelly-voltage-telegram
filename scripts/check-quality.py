#!/usr/bin/env python3
"""Check simple project rules that are not covered by Ruff alone."""

from __future__ import annotations

import argparse
import ast
from collections import defaultdict
from pathlib import Path

MAX_FUNCTION_LINES = 40
MAX_LINE_LENGTH = 79


def parse_args() -> argparse.Namespace:
    """Parse command-line options for the quality checker."""

    parser = argparse.ArgumentParser(
        description="Check function size, docstrings, logging and duplicates."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Project root to check (default: repository root).",
    )
    return parser.parse_args()


def python_files(root: Path) -> list[Path]:
    """Return Python source files that belong to the project."""

    files = [
        root / "app.py",
        root / "advanced_features.py",
        root / "apartment_phases.py",
        root / "dtek_correlation.py",
        root / "dtek_events.py",
        root / "dtek_schedule.py",
        root / "voltage_warning.py",
        root / "update_info.py",
        root / "telegram_i18n.py",
        root / "notification_features.py",
        root / "manual_dtek_forecast.py",
        root / "outage_data_ua.py",
        root / "yasno_schedule.py",
        root / "yasno_cache.py",
        root / "yasno_handoff.py",
        root / "telegram_channel_yasno.py",
        root / "telegram_schedule_periods.py",
        root / "telegram_widget_data.py",
        root / "telegram_widget_fallback.py",
    ]
    files.extend(sorted((root / "scripts").glob("*.py")))
    files.extend(sorted((root / "tests").glob("*.py")))
    return [path for path in files if path.is_file()]


def function_key(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Return a stable representation used to find exact duplicates."""

    clone = ast.parse(ast.unparse(node)).body[0]
    clone.name = "_function_"
    return ast.dump(clone, include_attributes=False)


def check_function(
    path: Path,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    production: bool,
    errors: list[str],
) -> None:
    """Check one function for size and required documentation."""

    end = node.end_lineno or node.lineno
    size = end - node.lineno + 1
    if size > MAX_FUNCTION_LINES:
        errors.append(
            f"{path}:{node.lineno}: {node.name} has {size} lines "
            f"(maximum {MAX_FUNCTION_LINES})"
        )
    if production and ast.get_docstring(node) is None:
        errors.append(
            f"{path}:{node.lineno}: missing docstring for {node.name}"
        )


def check_tree(
    path: Path,
    tree: ast.Module,
    errors: list[str],
    duplicates: dict[str, list[str]],
) -> None:
    """Check documentation, logging imports and function structure."""

    production = "tests" not in path.parts
    if production and ast.get_docstring(tree) is None:
        errors.append(f"{path}:1: missing module docstring")
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            check_function(path, node, production, errors)
            duplicates[function_key(node)].append(
                f"{path}:{node.lineno}:{node.name}"
            )
        elif production and isinstance(node, ast.ClassDef):
            if ast.get_docstring(node) is None:
                errors.append(
                    f"{path}:{node.lineno}: missing docstring for {node.name}"
                )
        elif isinstance(node, ast.Import):
            if any(alias.name == "logging" for alias in node.names):
                errors.append(f"{path}:{node.lineno}: use loguru, not logging")
        elif isinstance(node, ast.ImportFrom) and node.module == "logging":
            errors.append(f"{path}:{node.lineno}: use loguru, not logging")


def check_file(
    root: Path,
    path: Path,
    errors: list[str],
    duplicates: dict[str, list[str]],
) -> None:
    """Check one Python file and collect readable error messages."""

    text = path.read_text(encoding="utf-8")
    relative = path.relative_to(root)
    for number, line in enumerate(text.splitlines(), 1):
        if len(line) > MAX_LINE_LENGTH:
            errors.append(
                f"{relative}:{number}: line has {len(line)} characters "
                f"(maximum {MAX_LINE_LENGTH})"
            )
    tree = ast.parse(text, filename=str(relative))
    check_tree(relative, tree, errors, duplicates)


def run_checks(root: Path) -> list[str]:
    """Run all project checks and return every discovered problem."""

    errors: list[str] = []
    duplicates: dict[str, list[str]] = defaultdict(list)
    for path in python_files(root):
        check_file(root, path, errors, duplicates)
    for locations in duplicates.values():
        if len(locations) > 1:
            joined = ", ".join(locations)
            errors.append(f"exact duplicate function bodies: {joined}")
    return errors


def main() -> int:
    """Run the checker and print one clear PASS or FAIL summary."""

    args = parse_args()
    errors = run_checks(args.root.resolve())
    if errors:
        print("QUALITY CHECK: FAIL")
        for error in errors:
            print(f"- {error}")
        return 1
    print("QUALITY CHECK: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
