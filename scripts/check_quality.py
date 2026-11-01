#!/usr/bin/env python3
"""Check simple maintainability rules that Ruff does not cover directly.

The project keeps these checks small and explicit so contributors can see
exactly why a build failed. Ruff remains responsible for PEP 8 style and
common Python mistakes.
"""

from __future__ import annotations

import argparse
import ast
import sys
from collections import defaultdict
from pathlib import Path

MAX_FUNCTION_LINES = 40
SKIP_PARTS = {".git", ".venv", "venv", "__pycache__"}


def python_files(root: Path) -> list[Path]:
    """Return Python files that belong to the repository."""

    return sorted(
        path
        for path in root.rglob("*.py")
        if not any(part in SKIP_PARTS for part in path.parts)
    )


def function_length(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """Return the source length of one function, including decorators."""

    first_line = min([node.lineno, *(item.lineno for item in node.decorator_list)])
    return (node.end_lineno or node.lineno) - first_line + 1


def function_name(path: Path, node: ast.AST) -> str:
    """Build a readable location for a function-related error."""

    name = getattr(node, "name", "<unknown>")
    line = getattr(node, "lineno", 0)
    return f"{path}:{line}: {name}"


def has_docstring(node: ast.AST) -> bool:
    """Return whether an AST node starts with a Python docstring."""

    return bool(ast.get_docstring(node, clean=False))


def duplicate_function_key(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Create a structural key that ignores only the function name."""

    copy = ast.parse(ast.unparse(node)).body[0]
    copy.name = "_function_"
    return ast.dump(copy, include_attributes=False)


def inspect_tree(
    path: Path, tree: ast.Module
) -> tuple[list[str], list[tuple[str, str]]]:
    """Inspect one parsed file and return problems and function signatures."""

    problems: list[str] = []
    signatures: list[tuple[str, str]] = []
    if not has_docstring(tree):
        problems.append(f"{path}:1: module is missing a docstring")
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if function_length(node) > MAX_FUNCTION_LINES:
                problems.append(
                    f"{function_name(path, node)} exceeds {MAX_FUNCTION_LINES} lines"
                )
            if not has_docstring(node):
                problems.append(f"{function_name(path, node)} is missing a docstring")
            signatures.append((duplicate_function_key(node), function_name(path, node)))
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [alias.name for alias in node.names]
            if node.module == "logging" or "logging" in names:
                problems.append(f"{path}:{node.lineno}: use loguru instead of logging")
    return problems, signatures


def inspect_file(path: Path) -> tuple[list[str], list[tuple[str, str]]]:
    """Parse and inspect one Python source file."""

    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as exc:
        return [f"{path}: cannot parse file: {exc}"], []
    return inspect_tree(path, tree)


def duplicate_problems(signatures: list[tuple[str, str]]) -> list[str]:
    """Report functions whose normalized syntax is exactly duplicated."""

    locations: dict[str, list[str]] = defaultdict(list)
    for key, location in signatures:
        locations[key].append(location)
    return [
        "duplicate function bodies: " + " | ".join(items)
        for items in locations.values()
        if len(items) > 1
    ]


def run(root: Path) -> list[str]:
    """Run all custom checks and return human-readable failures."""

    problems: list[str] = []
    signatures: list[tuple[str, str]] = []
    for path in python_files(root):
        file_problems, file_signatures = inspect_file(path)
        problems.extend(file_problems)
        signatures.extend(file_signatures)
    problems.extend(duplicate_problems(signatures))
    return problems


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line parser for the quality checker."""

    parser = argparse.ArgumentParser(
        description="Check project function length, docstrings and logging rules."
    )
    parser.add_argument(
        "root",
        nargs="?",
        default=".",
        type=Path,
        help="Repository root to scan. Default: current directory.",
    )
    return parser


def main() -> int:
    """Run checks from the command line and return a stable exit code."""

    args = build_parser().parse_args()
    problems = run(args.root.resolve())
    for problem in problems:
        print(f"FAIL: {problem}")
    if problems:
        print(f"FAIL: {len(problems)} maintainability problem(s) found")
        return 1
    print("PASS: maintainability checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
