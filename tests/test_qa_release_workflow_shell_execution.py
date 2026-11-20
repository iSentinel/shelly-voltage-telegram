"""Execute both real release guard shell steps with mock offline CLIs."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = ("release.yml", "image.yml")
STEP_NAME = "- name: Verify protected GitHub release controls"

MOCK_GH = """#!/usr/bin/env python3
import json
import os
import sys

mode = os.environ.get("SVT_FAKE_GH_MODE", "empty")
args = sys.argv[1:]
if len(args) < 2 or args[0] != "api":
    sys.exit(4)
if mode == "offline":
    sys.exit(6)
resource = args[1]
ruleset = {
    "id": 42,
    "target": "tag",
    "enforcement": "active",
    "bypass_actors": [{
        "actor_id": 83760791,
        "actor_type": "User",
        "bypass_mode": "always",
    }],
    "conditions": {
        "ref_name": {"include": ["refs/tags/v*"], "exclude": []}
    },
    "rules": [{"type": "creation"}],
}
if mode == "bypass":
    ruleset["bypass_actors"] = [{
        "actor_id": 12,
        "actor_type": "Team",
        "bypass_mode": "always",
    }]
environment = {
    "total_count": 1,
    "environments": [{
        "name": "shelly-release-approval",
        "can_admins_bypass": False,
        "protection_rules": [{
            "type": "required_reviewers",
            "prevent_self_review": False,
    "reviewers": [{
        "type": "User", "reviewer": {"id": 83760791},
    }],
        }],
    }],
}
if resource.endswith("rulesets?includes_parents=true"):
    if mode != "empty":
        print(42)
elif resource.endswith("rulesets/42"):
    print(json.dumps(ruleset))
elif resource.endswith("/shelly-voltage-telegram"):
    print(json.dumps({
        "full_name": "iSentinel/shelly-voltage-telegram",
        "private": False,
        "owner": {
            "login": "iSentinel", "id": 83760791, "type": "User",
        },
    }))
elif resource.endswith("environments"):
    print(json.dumps({"total_count": 0, "environments": []}
                     if mode == "empty" else environment))
else:
    sys.exit(7)
"""

MOCK_JQ = """#!/usr/bin/env python3
import json
import sys
if sys.argv[1:] != ["-s", "."]:
    sys.exit(5)
print(json.dumps([json.loads(line) for line in sys.stdin if line.strip()]))
"""


def shell_step(filename: str) -> str:
    """Extract exactly one tag protection step's YAML literal shell block."""
    lines = (ROOT / ".github/workflows" / filename).read_text().splitlines()
    start = next(
        i for i, line in enumerate(lines) if line.strip() == STEP_NAME
    )
    block = lines[start:]
    run = next(i for i, line in enumerate(block) if line.strip() == "run: |")
    content = []
    for line in block[run + 1 :]:
        if line.startswith("      - name:"):
            break
        if line.strip() and not line.startswith("          "):
            raise AssertionError("unexpected YAML shell indentation")
        content.append(line[10:] if line else "")
    if not content:
        raise AssertionError("missing workflow guard shell")
    return "\n".join(content)


class WorkflowGuardShellTests(unittest.TestCase):
    """Mock external commands without contacting production or GitHub."""

    def setUp(self) -> None:
        """Supply offline replacements for GitHub CLI, Git and jq."""
        temp = tempfile.TemporaryDirectory(prefix="svt-guard-shell-")
        self.addCleanup(temp.cleanup)
        self.bin = Path(temp.name)
        self.executable("gh", MOCK_GH)
        self.executable("jq", MOCK_JQ)
        self.executable(
            "git",
            "#!/bin/sh\n"
            '[ "$1" = rev-parse ] && [ "$2" = HEAD ] || exit 9\n'
            'printf "%s\\n" "' + "a" * 40 + '"\n',
        )

    def executable(self, name: str, source: str) -> None:
        """Place one private executable command stub in the test-only PATH."""
        file = self.bin / name
        file.write_text(source, encoding="utf-8")
        file.chmod(0o755)

    def execute(self, filename: str, mode: str) -> subprocess.CompletedProcess:
        """Run the actual YAML shell block in a controlled local subprocess."""
        env = os.environ.copy()
        env.update(
            {
                "PATH": str(self.bin) + os.pathsep + env["PATH"],
                "SVT_FAKE_GH_MODE": mode,
                "GITHUB_REPOSITORY": "iSentinel/shelly-voltage-telegram",
                "GITHUB_WORKSPACE": str(ROOT),
                "GH_TOKEN": "synthetic-offline-only",
            }
        )
        if mode == "no_token":
            env["GH_TOKEN"] = ""
        return subprocess.run(
            ["bash", "-euo", "pipefail", "-c", shell_step(filename)],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

    def test_missing_protection_rejects_both_publish_workflows(self) -> None:
        """The real shell scripts must exit nonzero with empty GitHub rules."""
        for file in FILES:
            with self.subTest(file=file):
                result = self.execute(file, "empty")
                self.assertEqual(result.returncode, 3, result.stderr)
                report = json.loads(result.stdout)
                self.assertEqual(report["status"], "BLOCKED")
                self.assertFalse(report["release_authorized"])

    def test_mock_protected_rules_only_approve_qa_configuration(self) -> None:
        """A positive synthetic ruleset must not authorize any deployment."""
        for file in FILES:
            with self.subTest(file=file):
                result = self.execute(file, "protected")
                self.assertEqual(result.returncode, 0, result.stderr)
                report = json.loads(result.stdout)
                self.assertEqual(
                    report["status"], "PASS_QA_GITHUB_GUARD_CONFIGURATION_ONLY"
                )
                self.assertFalse(report["release_authorized"])
                self.assertFalse(report["production_modified"])

    def test_unavailable_gh_api_fails_closed_in_both_workflows(self) -> None:
        """A GitHub permission or API outage cannot be treated as approval."""
        for file in FILES:
            with self.subTest(file=file):
                result = self.execute(file, "offline")
                self.assertNotEqual(result.returncode, 0)

    def test_missing_release_read_token_fails_closed(self) -> None:
        """The protected owner API credential cannot be optional."""
        for file in FILES:
            with self.subTest(file=file):
                result = self.execute(file, "no_token")
                self.assertNotEqual(result.returncode, 0)

    def test_tag_bypass_actor_rejects_both_workflows(self) -> None:
        """An explicit ruleset creation bypass is not verified protection."""
        for file in FILES:
            with self.subTest(file=file):
                result = self.execute(file, "bypass")
                self.assertEqual(result.returncode, 3, result.stderr)
                report = json.loads(result.stdout)
                self.assertIn(
                    "TAG_CREATION_RULESET_UNVERIFIED",
                    report["missing_guards"],
                )


if __name__ == "__main__":
    unittest.main()
