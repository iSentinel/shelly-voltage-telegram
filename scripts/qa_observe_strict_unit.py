"""Read-only observation of an inactive strict QA supervisor unit."""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import stat
from pathlib import Path

from cutover_journal import JournalBlocked
from qa_observe_legacy_stop import CONTAINER_ID, QA_NAME, query
from qa_observe_strict_receipt import ROOT, STAGE, helper

HOOKS = (
    "ExecCondition",
    "ExecStartPre",
    "ExecStartPost",
    "ExecReload",
    "ExecStopPost",
)
FIELDS = (
    "LoadState",
    "ActiveState",
    "UnitFileState",
    "NeedDaemonReload",
    "FragmentPath",
    "DropInPaths",
    "Job",
    "Type",
    "Restart",
    "User",
    "ExecStart",
    "ExecStop",
    *HOOKS,
)


def expected_commands(args: argparse.Namespace) -> tuple[str, str]:
    """Require a strict receipt and immutable stop target."""
    start = (
        f"/usr/bin/python3 -B {ROOT}/supervised-container.py "
        f"--container {args.container} --database {args.database} "
        f"--fence-dir {args.fence_dir} --receipt {STAGE}/deployed.json "
        f"--source-version {args.source_version} --require-pair-receipt "
        f"--source-archive-dir {STAGE}"
    )
    return start, f"/usr/bin/docker stop -t 10 {args.identity}"


def expected_unit(args: argparse.Namespace) -> str:
    """Require one exact staged file with no unobserved startup hooks."""
    start, stop = expected_commands(args)
    return (
        "[Unit]\nDescription=Disposable strict supervisor rehearsal\n"
        "After=docker.service\nRequires=docker.service\n"
        "[Service]\nType=simple\nRestart=no\n"
        f"ExecStart={start}\nExecStop={stop}\n"
        "[Install]\nWantedBy=multi-user.target\n"
    )


def validate_args(args: argparse.Namespace) -> None:
    """Restrict the adapter to root-owned disposable QA paths and names."""
    if (
        not args.qa_only
        or os.geteuid() != 0
        or socket.gethostname() != "shelly-docker-reboot-qa"
        or not QA_NAME.fullmatch(args.container)
        or not QA_NAME.fullmatch(args.unit.removesuffix(".service"))
        or not args.unit.endswith(".service")
        or not CONTAINER_ID.fullmatch(args.identity)
    ):
        raise JournalBlocked("STRICT_UNIT_QA_REQUIRED")
    for path, parent in (
        (args.database, Path("/var/tmp")),
        (args.fence_dir, Path("/var/lib")),
        (args.source_version, ROOT.parent),
    ):
        if (
            not path.is_absolute()
            or path != path.resolve()
            or not path.is_relative_to(parent)
            or not re.fullmatch(r"/[a-zA-Z0-9_./-]+", str(path))
        ):
            raise JournalBlocked("STRICT_UNIT_PATH_INVALID")


def unit_fields(unit: str) -> dict:
    """Read effective systemd fields, refusing incomplete or duplicate data."""
    raw = query(
        [
            "systemctl",
            "show",
            unit,
            "--no-pager",
            "--all",
            "--property=" + ",".join(FIELDS),
        ]
    )
    result = {}
    for line in raw.splitlines():
        key, sep, value = line.partition("=")
        if not sep or key not in FIELDS or key in result:
            raise JournalBlocked("STRICT_UNIT_FIELDS_INVALID")
        result[key] = value
    if set(FIELDS) - set(result) - set(HOOKS):
        raise JournalBlocked("STRICT_UNIT_FIELDS_INCOMPLETE")
    return result


def check_fields(fields: dict, args: argparse.Namespace, parse) -> None:
    """Reject hooks, unexpected commands, restart owners and loaded drift."""
    expected = {
        "LoadState": "loaded",
        "ActiveState": "inactive",
        "UnitFileState": "disabled",
        "NeedDaemonReload": "no",
        "FragmentPath": "/etc/systemd/system/" + args.unit,
        "DropInPaths": "",
        "Type": "simple",
        "Restart": "no",
    }
    if (
        any(fields.get(k) != v for k, v in expected.items())
        or fields.get("Job") not in ("", "0")
        or fields.get("User") not in ("", "root")
        or any(fields.get(k) != "" for k in HOOKS)
    ):
        raise JournalBlocked("STRICT_UNIT_POLICY_MISMATCH")
    actual = parse(fields["ExecStart"]), parse(fields["ExecStop"])
    if actual != expected_commands(args):
        raise JournalBlocked("STRICT_UNIT_COMMAND_MISMATCH")


def observe(args: argparse.Namespace) -> dict:
    """Verify loaded structure only; no claim of receipt or sole ownership."""
    validate_args(args)
    boot = helper("boot-ownership-audit.py")
    supervisor = helper("supervised-container.py")
    path = Path("/etc/systemd/system") / args.unit
    supervisor.require_root_ancestors(path)
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or info.st_nlink != 1
        or info.st_mode & 0o022
    ):
        raise JournalBlocked("STRICT_UNIT_FILE_UNSAFE")
    text = path.read_text()
    if text != expected_unit(args):
        raise JournalBlocked("STRICT_UNIT_FILE_CONTENT_MISMATCH")
    fields = unit_fields(args.unit)
    # systemctl can omit empty command arrays, even with --all.
    # Only the exact file plus loaded policy checks can justify omission.
    normalized = {**dict.fromkeys(HOOKS, ""), **fields}
    check_fields(normalized, args, boot.effective_exec)
    if unit_fields(args.unit) != fields or path.read_text() != text:
        raise JournalBlocked("STRICT_UNIT_CHANGED")
    return {
        "status": "PASS_QA_STRICT_UNIT_STRUCTURE_ONLY",
        "unit": args.unit,
        "container_id": args.identity,
        "receipt_verified": False,
        "all_writers_verified": False,
        "release_authorized": False,
        "production_modified": False,
    }


def main() -> int:
    """Emit an observation, with no operation to install or start a unit."""
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--qa-only", action="store_true", required=True)
    for name in ("unit", "container", "identity"):
        cli.add_argument("--" + name, required=True)
    for name in ("database", "fence-dir", "source-version"):
        cli.add_argument("--" + name, type=Path, required=True)
    try:
        result = observe(cli.parse_args())
    except (ValueError, OSError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": type(exc).__name__}))
        return 3
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
