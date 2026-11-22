"""Observe retired selected Telegram editor and stopped QA container."""

from __future__ import annotations

import argparse
import json
import os
import socket
from pathlib import Path

from cutover_journal import JournalBlocked
from qa_observe_legacy_stop import (
    CONTAINER_ID,
    QA_NAME,
    container_state,
    query,
)

COMMON = (
    "LoadState",
    "ActiveState",
    "SubState",
    "UnitFileState",
    "NeedDaemonReload",
    "Job",
)


def fields(name: str, service: bool) -> dict:
    """Read the persistent mask and live manager state."""
    keys = COMMON + (("MainPID", "ControlPID") if service else ())
    raw = query(
        [
            "systemctl",
            "show",
            name,
            "--no-pager",
            "--all",
            "--property=" + ",".join(keys),
        ]
    )
    result = {}
    for line in raw.splitlines():
        key, sep, value = line.partition("=")
        if not sep or key not in keys or key in result:
            raise JournalBlocked("EDITOR_FIELDS_INVALID")
        result[key] = value
    if set(result) != set(keys):
        raise JournalBlocked("EDITOR_FIELDS_INCOMPLETE")
    path = Path("/etc/systemd/system") / name
    result["mask"] = path.is_symlink() and path.readlink() == Path("/dev/null")
    return result


def check_unit(state: dict, service: bool) -> None:
    """Require persistent retirement, inactivity and no queued execution."""
    expected = {
        "LoadState": "masked",
        "ActiveState": "inactive",
        "SubState": "dead",
        "UnitFileState": "masked",
        "NeedDaemonReload": "no",
        "mask": True,
    }
    if (
        any(state.get(key) != value for key, value in expected.items())
        or state.get("Job") not in ("", "0")
        or (
            service
            and (state.get("MainPID") != "0" or state.get("ControlPID") != "0")
        )
    ):
        raise JournalBlocked("EDITOR_NOT_RETIRED")


def check_container(state: dict, identity: str) -> None:
    """Do not assume a stopped systemd process drained Docker exec work."""
    if (
        state.get("id") != identity
        or state.get("running") is not False
        or state.get("restarting") is not False
        or state.get("state") not in ("created", "exited")
        or state.get("restart") != "no"
        or state.get("network") != "none"
    ):
        raise JournalBlocked("EDITOR_CONTAINER_NOT_QUIESCENT")


def observe(args: argparse.Namespace) -> dict:
    """Verify named owners only; never stop, mask or authorize anything."""
    if (
        args.qa_only is not True
        or os.geteuid() != 0
        or socket.gethostname() != "shelly-docker-reboot-qa"
        or not QA_NAME.fullmatch(args.editor)
        or not QA_NAME.fullmatch(args.container)
        or not CONTAINER_ID.fullmatch(args.identity)
    ):
        raise JournalBlocked("EDITOR_DRAIN_QA_REQUIRED")
    timer, service = args.editor + ".timer", args.editor + ".service"
    a, b, c = (
        fields(timer, False),
        fields(service, True),
        container_state(args.container),
    )
    check_unit(a, False)
    check_unit(b, True)
    check_container(c, args.identity)
    if (a, b, c) != (
        fields(timer, False),
        fields(service, True),
        container_state(args.container),
    ):
        raise JournalBlocked("EDITOR_OBSERVATION_CHANGED")
    return {
        "status": "PASS_QA_SELECTED_EDITOR_DRAIN_ONLY",
        "editor": args.editor,
        "container_id": args.identity,
        "remote_delivery_reconciled": False,
        "all_editors_verified": False,
        "release_authorized": False,
        "production_modified": False,
    }


def main() -> int:
    """Emit bounded observation only."""
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--qa-only", action="store_true", required=True)
    for key in ("editor", "container", "identity"):
        cli.add_argument("--" + key, required=True)
    try:
        result = observe(cli.parse_args())
    except (ValueError, OSError) as exc:
        reason = (
            str(exc) if isinstance(exc, JournalBlocked) else type(exc).__name__
        )
        print(json.dumps({"status": "BLOCKED", "reason": reason}))
        return 3
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
