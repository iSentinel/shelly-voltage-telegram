#!/usr/bin/env python3
"""Review a pinned mode plan against signed source and local image facts."""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import stat
import subprocess
import tarfile
from pathlib import Path


def load(name: str):
    """Import a sibling QA tool without invoking its CLI."""
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).with_name(name + ".py")
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BOUND = load("qa-bound-offline-pair")
PLAN = load("qa-same-runtime-recovery-plan")
PAIR = BOUND.PAIR
OCI = BOUND.OCI


def source_tree(root: Path, receipt: dict) -> ast.Module:
    """Read one bounded app.py from the exact retained archive."""
    directory = root / "release-archive"
    PAIR.SUP.verify_paired_source_archive(receipt, directory)
    prefix = "shelly-voltage-telegram-" + receipt["version"]
    archive = directory / (prefix + ".tar.gz")
    with tarfile.open(archive, "r:gz") as source:
        members = [m for m in source if m.name == prefix + "/app.py"]
        if (
            len(members) != 1
            or not members[0].isfile()
            or not 0 < members[0].size <= 2 * 1024 * 1024
        ):
            raise ValueError("ambiguous signed app source")
        stream = source.extractfile(members[0])
        if stream is None:
            raise ValueError("missing signed app source")
        with stream:
            return ast.parse(stream.read())


def class_member(tree: ast.Module, name: str, member: str, kind):
    """Locate a unique top-level class and named member."""
    classes = [
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name
    ]
    if len(classes) != 1:
        raise ValueError("signed source feature absent")
    matches = [
        n
        for n in classes[0].body
        if isinstance(n, kind) and getattr(n, "name", None) == member
    ]
    if len(matches) != 1:
        raise ValueError("signed source feature absent")
    return matches[0]


def feature_present(tree: ast.Module) -> None:
    """Require the known pause branch; this is not runtime qualification."""
    run = class_member(tree, "Monitor", "run", ast.FunctionDef)
    class_member(tree, "Monitor", "report_loop", ast.FunctionDef)
    class_member(tree, "Monitor", "run_monitor_loop", ast.FunctionDef)
    expected = ast.parse(
        'if self.cfg.scheduled_reports_mode != "enabled":\n'
        '    logger.warning("Scheduled reports paused by startup policy")\n'
        "    self.run_monitor_loop()\n"
        "    return\n"
    ).body[0]
    if not any(ast.dump(node) == ast.dump(expected) for node in run.body):
        raise ValueError("signed source feature absent")
    configs = [
        n
        for n in tree.body
        if isinstance(n, ast.ClassDef) and n.name == "Config"
    ]
    if len(configs) != 1 or not any(
        isinstance(n, ast.AnnAssign)
        and isinstance(n.target, ast.Name)
        and n.target.id == "scheduled_reports_mode"
        and isinstance(n.value, ast.Constant)
        and n.value.value == "enabled"
        for n in configs[0].body
    ):
        raise ValueError("signed source feature absent")


def digest_plan(record: dict) -> str:
    """Compute the canonical plan hash independently of its JSON wrapper."""
    return PLAN.sha(
        json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
    )


def read_plan(root: Path, anchor: str) -> dict:
    """Require a pinned non-authorizing plan and matching wrapper."""
    wrapper = PAIR.object_bytes(PAIR.private_bytes(root / "plan.json", 8192))
    record = wrapper["plan"]
    if (
        set(wrapper) != {"status", "plan", "plan_sha256"}
        or wrapper["status"] != "PASS_QA_MODE_PLAN_ONLY"
        or not PLAN.HEX.fullmatch(anchor)
        or wrapper["plan_sha256"] != anchor
        or digest_plan(record) != anchor
    ):
        raise ValueError("plan anchor mismatch")
    return record


def check_runtime(root: Path, info: dict, record: dict) -> None:
    """Require a stopped exact fixture, source mode and database identity."""
    host = info["HostConfig"]
    if (
        info["State"]["Running"] is not False
        or info["Id"] != record["container_id"]
        or info["Image"] != record["image_id"]
        or info["Config"]["Image"] != record["image_id"]
        or host["NetworkMode"] != "none"
        or host["RestartPolicy"]["Name"] != "no"
    ):
        raise ValueError("runtime identity changed")
    BOUND.check_host(info)
    PAIR.SUP.validate_report_mode(info, record["source_mode"])
    mounts = [
        (m["Destination"], m["Source"], m["Type"], m["RW"])
        for m in info["Mounts"]
    ]
    expected = [
        (target, str(root / name), "bind", True)
        for target, name in (("/data", "data"), ("/run/svt-fence", "fence"))
    ]
    if sorted(mounts) != sorted(expected):
        raise ValueError("runtime mounts changed")
    db = (root / "data/qa.sqlite3").lstat()
    if (
        not stat.S_ISREG(db.st_mode)
        or db.st_nlink != 1
        or (db.st_dev, db.st_ino)
        != (record["database_device"], record["database_inode"])
    ):
        raise ValueError("database identity changed")


def verify_feature_pair(root, info, image, record, receipt, online):
    """Bind full online and offline proof before static feature inspection."""
    BOUND.check_online(root, record, online)
    PAIR.SUP.verify_receipt(
        root / "deployed.json",
        root / "VERSION",
        True,
        PAIR.SUP.image_revision(info),
        root / "release-archive",
    )
    proof = OCI.verify_chain(
        root, record["pair"]["digest"], record["pair"]["source_commit"]
    )
    if proof["image_id"] != info["Image"]:
        raise ValueError("local image binding mismatch")
    OCI.verify_local(proof, image)
    feature_present(source_tree(root, receipt))


def review(
    root: Path, info: dict, image: dict, online: str, anchor: str
) -> dict:
    """Recompute the plan and require evidence from the same exact pair."""
    record = read_plan(root, anchor)
    receipt = PAIR.object_bytes(
        PAIR.private_bytes(root / "deployed.json", 8192)
    )
    raw = PAIR.private_bytes(root / ".env", 65536)
    check_runtime(root, info, record)
    db = (root / "data/qa.sqlite3").lstat()
    expected = PLAN.plan(
        receipt,
        raw,
        record["source_mode"],
        record["target_mode"],
        info["Image"],
        info["Id"],
        db.st_dev,
        db.st_ino,
    )
    if expected != record or digest_plan(expected) != anchor:
        raise ValueError("mode plan no longer matches")
    verify_feature_pair(root, info, image, record, receipt, online)
    return {
        "status": "PASS_QA_PLAN_STATIC_FEATURE_ONLY",
        "plan_sha256": anchor,
        "online_evidence_sha256": online,
        "runtime_qualified": False,
        "owner_approved": False,
        "startup_authorized": False,
        "deployment_authorized": False,
    }


def fixture_review(args: argparse.Namespace) -> dict:
    """Run the review within the isolated protected gates."""
    PAIR.TX.require_qa(args.root)
    with PAIR.TX.exclusive(args.root):
        if PAIR.SUP._pending_under_gate(args.root / "data/qa.sqlite3"):
            raise ValueError("update pending")
        info = PAIR.SUP.inspect("svt-qa-" + args.root.name)
        return review(
            args.root,
            info,
            PAIR.inspect_image(info["Image"]),
            args.online_evidence_sha256,
            args.plan_sha256,
        )


def main() -> int:
    """Review only an offline fixture under the existing transaction gates."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--online-evidence-sha256", required=True)
    parser.add_argument("--plan-sha256", required=True)
    args = parser.parse_args()
    try:
        result = fixture_review(args)
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        EOFError,
        SyntaxError,
        tarfile.TarError,
        subprocess.SubprocessError,
        PAIR.TX.WriterFenceError,
    ) as exc:
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "reason": str(exc)
                    if str(exc) == "signed source feature absent"
                    else type(exc).__name__,
                }
            )
        )
        return 3
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
