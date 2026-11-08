"""Report source-change scope without authorizing an EM-50 release."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

MAX_OUTPUT_BYTES = 262144
MAX_CHANGED_FILES = 1000
REF_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}")


def validate_ref(value: str) -> None:
    """Reject arguments that could be confused with Git options."""

    if not REF_PATTERN.fullmatch(value) or ".." in value:
        raise ValueError("invalid revision name")


def run_git(repo: Path, *arguments: str) -> bytes:
    """Run a bounded, noninteractive Git metadata query."""

    result = subprocess.run(
        ["git", "-C", str(repo), *arguments],
        capture_output=True,
        check=False,
        timeout=20,
    )
    if result.returncode or len(result.stdout) > MAX_OUTPUT_BYTES:
        raise ValueError("Git metadata query failed or exceeded limit")
    return result.stdout


def validate_path(value: str) -> None:
    """Treat unusual or private source paths as a hard review blocker."""

    parts = value.split("/")
    if (
        not value
        or value.startswith("/")
        or len(value) > 240
        or any(part in {"", ".", ".."} for part in parts)
        or any(ord(char) < 32 for char in value)
    ):
        raise ValueError("unsafe changed-file path")


def is_private(value: str) -> bool:
    """Never report credentials or private evidence filenames."""

    parts = value.split("/")
    if any(part in {"secrets", "validation"} for part in parts):
        return True
    return any(
        part == ".env" or (part.startswith(".env.") and part != ".env.example")
        for part in parts
    )


def area_for(path: str) -> str:
    """Assign each changed source to a stable review workstream."""

    if path.startswith("tests/"):
        return "tests"
    if path.startswith("docs/") or path.endswith(".md"):
        return "docs"
    if path.startswith("scripts/"):
        return "scripts"
    if path.startswith(("deploy/", "systemd/", ".github/")):
        return "deployment"
    if path in {"Dockerfile", "compose.yaml", "requirements.txt"}:
        return "deployment"
    if "/" not in path and path.endswith(".py"):
        return "runtime"
    return "other"


def changed_paths(repo: Path, base: str, head: str) -> list[str]:
    """List all changed names relative to the candidate merge base."""

    validate_ref(base)
    validate_ref(head)
    raw = run_git(
        repo,
        "diff",
        "--name-status",
        "-z",
        "--no-renames",
        "--no-ext-diff",
        f"{base}...{head}",
        "--",
    )
    tokens = raw.rstrip(b"\0").split(b"\0")
    if len(tokens) % 2:
        raise ValueError("malformed Git status record")
    names = []
    for index in range(0, len(tokens), 2):
        code = tokens[index].decode("ascii")
        path = tokens[index + 1].decode("utf-8")
        if code not in {"A", "M", "D", "T"}:
            raise ValueError("unreviewable Git change status")
        validate_path(path)
        if is_private(path):
            raise ValueError("private source path in release diff")
        names.append(path)
    if not names or len(names) > MAX_CHANGED_FILES:
        raise ValueError("candidate diff empty or too large")
    return sorted(names)


def audit(repo: Path, base: str, head: str) -> dict:
    """Classify changes and always require human approval."""

    if not repo.is_dir():
        raise ValueError("repository directory unavailable")
    names = changed_paths(repo, base, head)
    areas = Counter(area_for(path) for path in names)
    critical = [
        path for path in names if area_for(path) not in {"docs", "tests"}
    ]
    return {
        "status": "REVIEW_REQUIRED",
        "changed_files": len(names),
        "by_area": dict(sorted(areas.items())),
        "review_required_files": critical,
        "review_required_count": len(critical),
        "source_review_approved": False,
        "release_authorized": False,
    }


def main() -> int:
    """Generate a source-only review manifest with stable exit codes."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--enforce", action="store_true")
    args = parser.parse_args()
    try:
        result = audit(args.repo, args.base, args.head)
    except (OSError, ValueError, UnicodeError, subprocess.TimeoutExpired):
        print("FAIL release_scope metadata_unavailable", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(f"REVIEW_REQUIRED changed_files={result['changed_files']}")
        print(f"review_required_files={result['review_required_count']}")
    return 2 if args.enforce else 0


if __name__ == "__main__":
    raise SystemExit(main())
