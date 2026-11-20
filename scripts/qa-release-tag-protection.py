#!/usr/bin/env python3
"""Audit GitHub tag and release environment guards without changing them."""

from __future__ import annotations

import argparse
import json
import re
import stat
from pathlib import Path

VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+-hw\Z")
SHA = re.compile(r"[0-9a-f]{40}\Z")
TAG_REFS = {"refs/tags/v*", "~ALL"}
ENVIRONMENT = "shelly-release-approval"
ENV_CANDIDATES = ("release.yml", "image.yml")


class ProtectionBlocked(ValueError):
    """The current release controls cannot be safely verified."""


def arguments() -> argparse.Namespace:
    """Use saved GitHub API evidence; never edit repository settings."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qa-only", action="store_true", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--rulesets-json", required=True, type=Path)
    parser.add_argument("--environments-json", required=True, type=Path)
    parser.add_argument("--workflows-dir", required=True, type=Path)
    parser.add_argument("--jsonl", action="store_true")
    return parser.parse_args()


def read_json(path: Path) -> object:
    """Load bounded single-link local snapshots without following symlinks."""
    if not path.is_absolute() or ".." in path.parts:
        raise ProtectionBlocked("TAG_GITHUB_SNAPSHOT_PATH_INVALID")
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ProtectionBlocked("TAG_GITHUB_SNAPSHOT_LINKED")
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or not 0 < info.st_size <= 1024 * 1024
    ):
        raise ProtectionBlocked("TAG_GITHUB_SNAPSHOT_INVALID")
    return json.loads(path.read_text(encoding="utf-8"))


def tag_ruleset_ready(items: object) -> bool:
    """Require an active tag-creation restriction for every stable v* tag."""
    if not isinstance(items, list):
        raise ProtectionBlocked("TAG_RULESETS_JSON_INVALID")
    for item in items:
        if not isinstance(item, dict):
            continue
        conditions = item.get("conditions")
        if not isinstance(conditions, dict):
            continue
        names = conditions.get("ref_name")
        if not isinstance(names, dict):
            continue
        includes = names.get("include")
        excludes = names.get("exclude", [])
        rules = item.get("rules")
        if not all(isinstance(x, list) for x in (includes, excludes, rules)):
            continue
        types = {rule.get("type") for rule in rules if isinstance(rule, dict)}
        if (
            item.get("target") == "tag"
            and item.get("enforcement") == "active"
            and TAG_REFS.intersection(includes)
            and not TAG_REFS.intersection(excludes)
            and "creation" in types
        ):
            return True
    return False


def approved_environment(data: object) -> bool:
    """Require at least one configured mandatory reviewer for publication."""
    if not isinstance(data, dict):
        raise ProtectionBlocked("TAG_ENVIRONMENTS_JSON_INVALID")
    environments = data.get("environments")
    if not isinstance(environments, list):
        raise ProtectionBlocked("TAG_ENVIRONMENTS_JSON_INVALID")
    for env in environments:
        if not isinstance(env, dict) or env.get("name") != ENVIRONMENT:
            continue
        rules = env.get("protection_rules")
        if not isinstance(rules, list):
            return False
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            reviewers = rule.get("reviewers")
            if (
                rule.get("type") == "required_reviewers"
                and isinstance(reviewers, list)
                and len(reviewers) >= 1
            ):
                return True
    return False


def protected_job(path: Path, name: str) -> bool:
    """Find reviewed environment on an exact release job only."""
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        raise ProtectionBlocked("TAG_WORKFLOW_FILE_UNSAFE")
    body = path.read_text(encoding="utf-8")
    job = re.search(rf"(?m)^  {re.escape(name)}:\s*$", body)
    if job is None:
        return False
    next_job = re.search(r"(?m)^  [a-zA-Z_][\w-]*:\s*$", body[job.end() :])
    segment = body[job.end() :]
    if next_job is not None:
        segment = segment[: next_job.start()]
    return bool(
        re.search(
            rf"(?m)^    environment:\s*{re.escape(ENVIRONMENT)}\s*$",
            segment,
        )
    )


def missing_guards(
    tags: bool, reviewed: bool, source: bool, image: bool
) -> list[str]:
    """List each independent release permission barrier not yet set."""
    missing = []
    if not tags:
        missing.append("TAG_CREATION_RULESET_UNVERIFIED")
    if not reviewed:
        missing.append("OWNER_REVIEW_ENVIRONMENT_UNPROTECTED")
    if not source:
        missing.append("SOURCE_PUBLISH_JOB_NOT_OWNER_GATED")
    if not image:
        missing.append("OCI_PUBLISH_JOB_NOT_OWNER_GATED")
    return missing


def evaluate(args: argparse.Namespace) -> dict:
    """Report tag creation and both publishing jobs as advisory controls."""
    if (
        not args.qa_only
        or not VERSION.fullmatch(args.version)
        or not SHA.fullmatch(args.commit)
    ):
        raise ProtectionBlocked("TAG_QA_IDENTITY_INVALID")
    rules = read_json(args.rulesets_json)
    environments = read_json(args.environments_json)
    tags = tag_ruleset_ready(rules)
    protected = approved_environment(environments)
    base = args.workflows_dir
    if not base.is_absolute() or base.is_symlink() or not base.is_dir():
        raise ProtectionBlocked("TAG_WORKFLOW_DIRECTORY_UNSAFE")
    source = protected_job(base / "release.yml", "release")
    image = protected_job(base / "image.yml", "build")
    missing = missing_guards(tags, protected, source, image)
    status = (
        "BLOCKED" if missing else "PASS_QA_GITHUB_GUARD_CONFIGURATION_ONLY"
    )
    return {
        "status": status,
        "version": args.version,
        "candidate_commit": args.commit,
        "missing_guards": missing,
        "public_snapshot_independently_authenticated": False,
        "release_specific_owner_approval": False,
        "source_oci_signed": False,
        "production_cutover_approved": False,
        "release_authorized": False,
        "rollback_authorized": False,
        "production_modified": False,
    }


def main() -> int:
    """Stable, non-mutating audit with no GitHub settings API writes."""
    args = arguments()
    try:
        report = evaluate(args)
    except (OSError, ValueError, TypeError) as error:
        report = {
            "status": "BLOCKED",
            "reason": (
                str(error)
                if isinstance(error, ProtectionBlocked)
                else "TAG_GUARD_EVIDENCE_UNAVAILABLE"
            ),
            "release_authorized": False,
            "rollback_authorized": False,
            "production_modified": False,
        }
    if args.jsonl:
        print(json.dumps(report, separators=(",", ":")))
    else:
        print("RELEASE TAG GUARD:", report["status"])
        if report.get("missing_guards"):
            print("missing:", ",".join(report["missing_guards"]))
    return 0 if report["status"].startswith("PASS_") else 3


if __name__ == "__main__":
    raise SystemExit(main())
