"""GitHub tag creation and release jobs need owner-review controls."""

from __future__ import annotations

import argparse
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "svt_tag_guard_qa", ROOT / "scripts/qa-release-tag-protection.py"
)
assert SPEC is not None and SPEC.loader is not None
GUARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GUARD)


class ReleaseTagProtectionTests(unittest.TestCase):
    """All fixtures represent disposable metadata, not live GitHub rules."""

    def setUp(self) -> None:
        """Prepare two workflow jobs with an optional reviewer environment."""
        owner = tempfile.TemporaryDirectory(prefix="svt-tag-qa-")
        self.addCleanup(owner.cleanup)
        root = Path(owner.name)
        self.rules = root / "rulesets.json"
        self.environments = root / "environments.json"
        self.workflows = root / "workflows"
        self.workflows.mkdir()
        self.args = argparse.Namespace(
            qa_only=True,
            version="0.5.26-hw",
            commit="a" * 40,
            rulesets_json=self.rules,
            environments_json=self.environments,
            workflows_dir=self.workflows,
            jsonl=True,
        )
        self.save(self.rules, [])
        self.save(
            self.environments,
            {
                "total_count": 0,
                "environments": [],
            },
        )
        self.workflow("release.yml", "release", owner=False)
        self.workflow("image.yml", "build", owner=False)

    def save(self, path: Path, contents: object) -> None:
        """Write a local GitHub API fixture with public-only values."""
        path.write_text(json.dumps(contents), encoding="utf-8")

    def workflow(self, filename: str, job: str, *, owner: bool) -> None:
        """Emit a minimal job with or without protected environment name."""
        content = f"name: QA\njobs:\n  {job}:\n"
        if owner:
            content += "    environment: shelly-release-approval\n"
        content += "    runs-on: ubuntu-latest\n"
        (self.workflows / filename).write_text(content)

    def add_ruleset(self) -> None:
        """Create a synthetic active protected v* tag-creation rule."""
        self.save(
            self.rules,
            [
                {
                    "target": "tag",
                    "enforcement": "active",
                    "bypass_actors": [],
                    "conditions": {
                        "ref_name": {
                            "include": ["refs/tags/v*"],
                            "exclude": [],
                        }
                    },
                    "rules": [{"type": "creation"}, {"type": "deletion"}],
                }
            ],
        )

    def add_environment(self) -> None:
        """Create a synthetic required reviewer deployment environment."""
        self.save(
            self.environments,
            {
                "total_count": 1,
                "environments": [
                    {
                        "name": "shelly-release-approval",
                        "protection_rules": [
                            {
                                "type": "required_reviewers",
                                "reviewers": [{"type": "User", "id": 100}],
                            }
                        ],
                    }
                ],
            },
        )

    def test_unprotected_repository_is_blocked(self) -> None:
        """An empty Github ruleset/environment inventory remains NO-GO."""
        result = GUARD.evaluate(self.args)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(len(result["missing_guards"]), 4)
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["production_modified"])

    def test_all_configured_still_not_deployment_approval(self) -> None:
        """Even ideal synthetic settings only mean configuration evidence."""
        self.add_ruleset()
        self.add_environment()
        for file, job in (("release.yml", "release"), ("image.yml", "build")):
            self.workflow(file, job, owner=True)
        result = GUARD.evaluate(self.args)
        self.assertEqual(
            result["status"], "PASS_QA_GITHUB_GUARD_CONFIGURATION_ONLY"
        )
        self.assertEqual(result["missing_guards"], [])
        for key in (
            "public_snapshot_independently_authenticated",
            "release_specific_owner_approval",
            "source_oci_signed",
            "production_cutover_approved",
            "release_authorized",
            "rollback_authorized",
            "production_modified",
        ):
            self.assertFalse(result[key])

    def test_required_reviewer_missing_still_blocked(self) -> None:
        """Environment name alone is not evidence of mandatory approval."""
        self.add_ruleset()
        self.add_environment()
        self.workflow("release.yml", "release", owner=True)
        self.workflow("image.yml", "build", owner=True)
        env = json.loads(self.environments.read_text())
        env["environments"][0]["protection_rules"] = []
        self.save(self.environments, env)
        self.assertIn(
            "OWNER_REVIEW_ENVIRONMENT_UNPROTECTED",
            GUARD.evaluate(self.args)["missing_guards"],
        )

    def test_one_ungated_image_workflow_blocks(self) -> None:
        """A protected source release cannot waive signed-image controls."""
        self.add_ruleset()
        self.add_environment()
        self.workflow("release.yml", "release", owner=True)
        self.assertIn(
            "OCI_PUBLISH_JOB_NOT_OWNER_GATED",
            GUARD.evaluate(self.args)["missing_guards"],
        )

    def test_reviewed_tag_conditional_image_environment(self) -> None:
        """The exact tag-only reviewed environment is accepted."""
        self.add_ruleset()
        self.add_environment()
        self.workflow("release.yml", "release", owner=True)
        (self.workflows / "image.yml").write_text(
            "jobs:\n  build:\n"
            f"    environment: {GUARD.IMAGE_ENVIRONMENT_EXPR}\n"
            "    runs-on: ubuntu-latest\n"
        )
        self.assertEqual(GUARD.evaluate(self.args)["missing_guards"], [])

    def test_unrecognized_image_environment_expression_denied(self) -> None:
        """A typo in a release context expression cannot pass QA."""
        self.add_ruleset()
        self.add_environment()
        self.workflow("release.yml", "release", owner=True)
        (self.workflows / "image.yml").write_text(
            "jobs:\n  build:\n"
            "    environment: ${{ github.ref_type == 'branch' "
            "&& 'shelly-release-approval' || 'ci-image-build' }}\n"
        )
        self.assertIn(
            "OCI_PUBLISH_JOB_NOT_OWNER_GATED",
            GUARD.evaluate(self.args)["missing_guards"],
        )

    def test_tag_rule_only_on_branch_is_insufficient(self) -> None:
        """Main-branch protection cannot control v* tag creation."""
        self.add_environment()
        self.save(
            self.rules,
            [
                {
                    "target": "branch",
                    "enforcement": "active",
                    "conditions": {
                        "ref_name": {"include": ["refs/heads/main"]}
                    },
                    "rules": [{"type": "creation"}],
                }
            ],
        )
        self.assertIn(
            "TAG_CREATION_RULESET_UNVERIFIED",
            GUARD.evaluate(self.args)["missing_guards"],
        )

    def test_bypass_actor_can_never_be_implicit_owner_approval(self) -> None:
        """An actor bypassing creation rules makes tag protection unproved."""
        for mode in ("always", "exempt", "pull_request"):
            with self.subTest(mode=mode):
                self.add_ruleset()
                data = json.loads(self.rules.read_text())
                data[0]["bypass_actors"] = [
                    {
                        "actor_id": 22,
                        "actor_type": "Team",
                        "bypass_mode": mode,
                    }
                ]
                self.save(self.rules, data)
                self.assertFalse(GUARD.tag_ruleset_ready(data))

    def test_missing_bypass_metadata_fails_closed(self) -> None:
        """A partial GitHub API ruleset cannot prove non-bypassability."""
        self.add_ruleset()
        data = json.loads(self.rules.read_text())
        del data[0]["bypass_actors"]
        self.assertFalse(GUARD.tag_ruleset_ready(data))

    def test_empty_explicit_bypass_list_is_reviewer_evidence_only(
        self,
    ) -> None:
        """No bypass actors permits a ruleset claim, never release approval."""
        self.add_ruleset()
        data = json.loads(self.rules.read_text())
        self.assertTrue(GUARD.tag_ruleset_ready(data))

    def test_non_active_ruleset_does_not_count(self) -> None:
        """A disabled rule is not release protection."""
        self.add_ruleset()
        data = json.loads(self.rules.read_text())
        data[0]["enforcement"] = "disabled"
        self.save(self.rules, data)
        self.assertFalse(GUARD.tag_ruleset_ready(data))

    def test_excluded_v_tags_cannot_be_claimed_as_protected(self) -> None:
        """An exclusion of all stable tags defeats a catch-all tag rule."""
        self.add_ruleset()
        data = json.loads(self.rules.read_text())
        data[0]["conditions"]["ref_name"]["exclude"] = ["refs/tags/v*"]
        self.save(self.rules, data)
        self.assertFalse(GUARD.tag_ruleset_ready(data))

    def test_workflow_environment_must_be_in_publish_job(self) -> None:
        """An environment on a different job is not source protection."""
        self.add_ruleset()
        self.add_environment()
        (self.workflows / "release.yml").write_text(
            "jobs:\n  lint:\n"
            "    environment: shelly-release-approval\n"
            "  release:\n"
            "    runs-on: ubuntu-latest\n",
        )
        self.assertFalse(
            GUARD.protected_job(self.workflows / "release.yml", "release")
        )

    def test_mutable_refs_fail_before_workflow_lookup(self) -> None:
        """Untrusted versions/commits never produce a ready result."""
        for key, value in (("commit", "HEAD"), ("version", "latest")):
            with self.subTest(key=key):
                setattr(self.args, key, value)
                with self.assertRaisesRegex(
                    GUARD.ProtectionBlocked, "TAG_QA_IDENTITY_INVALID"
                ):
                    GUARD.evaluate(self.args)
                setattr(
                    self.args,
                    key,
                    "a" * 40 if key == "commit" else "0.5.26-hw",
                )

    def test_linked_evidence_rejected(self) -> None:
        """Never follow an attacker-controlled local JSON symlink."""
        linked = self.rules.parent / "link.json"
        linked.symlink_to(self.rules)
        self.args.rulesets_json = linked
        with self.assertRaisesRegex(
            GUARD.ProtectionBlocked, "TAG_GITHUB_SNAPSHOT_LINKED"
        ):
            GUARD.evaluate(self.args)

    def test_missing_workflow_file_fails_closed(self) -> None:
        """Missing image publication job cannot be silently permitted."""
        self.add_ruleset()
        self.add_environment()
        (self.workflows / "image.yml").unlink()
        with self.assertRaisesRegex(
            GUARD.ProtectionBlocked, "TAG_WORKFLOW_FILE_UNSAFE"
        ):
            GUARD.evaluate(self.args)


if __name__ == "__main__":
    unittest.main()
