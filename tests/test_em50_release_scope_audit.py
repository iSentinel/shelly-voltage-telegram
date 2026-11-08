"""Exercise source-scope release audit using disposable Git repositories."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/em50-release-scope-audit.py"
SPEC = importlib.util.spec_from_file_location("em50_scope_audit", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


def git(repo: Path, *args: str) -> None:
    """Execute one local Git command without network access."""

    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        timeout=10,
    )


class ReleaseScopeTests(unittest.TestCase):
    """Prove that review remains mandatory even for validated Git metadata."""

    def setUp(self) -> None:
        """Build an isolated main branch and a candidate."""

        temporary = tempfile.TemporaryDirectory(prefix="em50-scope-test-")
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name) / "source"
        self.repo.mkdir()
        git(self.repo, "init", "--initial-branch=main")
        git(self.repo, "config", "user.name", "QA Robot")
        git(self.repo, "config", "user.email", "qa@example.invalid")
        self.add_source("docs/README.md", "baseline")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "base")
        git(self.repo, "switch", "-c", "candidate")

    def add_source(self, name: str, text: str) -> None:
        """Stage one source fixture with an exact predictable path."""

        target = self.repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def commit(self) -> None:
        """Save candidate changes for merge-base comparison."""

        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "candidate fixture")

    def test_runtime_and_deployment_require_review(self):
        """One passing status must never authorize a broad candidate."""

        self.add_source("app.py", "print('qa')")
        self.add_source("deploy/test.service", "[Unit]")
        self.add_source("docs/README.md", "updated")
        self.add_source("tests/test_app.py", "assert True")
        self.add_source("scripts/rollback.sh", "exit 0")
        self.commit()
        report = AUDIT.audit(self.repo, "main", "candidate")
        self.assertEqual(report["changed_files"], 5)
        self.assertEqual(report["review_required_count"], 3)
        self.assertEqual(report["by_area"]["runtime"], 1)
        self.assertEqual(report["by_area"]["deployment"], 1)
        self.assertFalse(report["release_authorized"])
        self.assertFalse(report["source_review_approved"])

    def test_private_source_never_appears_in_manifest(self):
        """Reject credential filenames without revealing their contents."""

        self.add_source("secrets/credentials", "private synthetic QA")
        self.commit()
        with self.assertRaisesRegex(ValueError, "private source"):
            AUDIT.audit(self.repo, "main", "candidate")

    def test_nested_private_and_traversal_paths_rejected(self):
        """Protect against private nested files and unsafe Git paths."""

        for path in ("../escape", "/absolute", "a//b", "a/./b"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                AUDIT.validate_path(path)
        self.assertTrue(AUDIT.is_private("config/.env.local"))
        self.assertFalse(AUDIT.is_private(".env.example"))

    def test_invalid_revision_names_rejected(self):
        """No revision may inject Git flags or three-dot syntax."""

        for name in ("--output", "main..candidate", "branch@{0}", ""):
            with self.subTest(name=name), self.assertRaises(ValueError):
                AUDIT.validate_ref(name)

    def test_empty_and_missing_revisions_fail_closed(self):
        """No empty Git diff or missing base can be called reviewed."""

        with self.assertRaises(ValueError):
            AUDIT.audit(self.repo, "main", "candidate")
        self.add_source("app.py", "new")
        self.commit()
        with self.assertRaises(ValueError):
            AUDIT.audit(self.repo, "missing", "candidate")

    def test_changed_file_limit_is_enforced(self):
        """Large candidate diffs cannot silently truncate review."""

        self.add_source("app.py", "new")
        self.add_source("scripts/check.sh", "exit 0")
        self.commit()
        original = AUDIT.MAX_CHANGED_FILES
        try:
            AUDIT.MAX_CHANGED_FILES = 1
            with self.assertRaises(ValueError):
                AUDIT.audit(self.repo, "main", "candidate")
        finally:
            AUDIT.MAX_CHANGED_FILES = original

    def test_cli_exit_codes_never_approve_release(self):
        """Normal scope report is readable; enforced review is blocked."""

        self.add_source("app.py", "new")
        self.commit()
        cmd = [
            sys.executable,
            str(SCRIPT),
            "--repo",
            str(self.repo),
            "--base",
            "main",
            "--head",
            "candidate",
            "--json",
        ]
        normal = subprocess.run(
            cmd, capture_output=True, text=True, timeout=10, check=False
        )
        report = json.loads(normal.stdout)
        self.assertEqual(normal.returncode, 0)
        self.assertEqual(report["status"], "REVIEW_REQUIRED")
        self.assertFalse(report["release_authorized"])
        guarded = subprocess.run(
            [*cmd, "--enforce"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(guarded.returncode, 2)


if __name__ == "__main__":
    unittest.main()
