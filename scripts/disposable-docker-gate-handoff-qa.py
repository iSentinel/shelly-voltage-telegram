#!/usr/bin/env python3
"""Test independent admission G with container B/W and host maintenance A."""

from __future__ import annotations

import argparse
import fcntl
import importlib.util
import json
import os
import sqlite3
import tempfile
import time
from contextlib import closing
from pathlib import Path

GUARD = Path(__file__).with_name("disposable-writer-fence-container-qa.py")
CONSENT = "OFFLINE_REAL_DOCKER_G_B_W_A_QA_ONLY"
CHILD = """
import fcntl
import signal
import sqlite3
import sys
import time
from pathlib import Path

stop_requested = False


def request_shutdown(_signum, _frame):
    global stop_requested
    stop_requested = True


signal.signal(signal.SIGTERM, request_shutdown)
role = sys.argv[1]
event = Path("/events") / (role + ".status")

def mark(text):
    event.write_text(text)

with Path("/locks/G").open("rb") as g:
    try:
        fcntl.flock(g, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except BlockingIOError:
        mark("DENIED_G")
        raise SystemExit(20)
    with Path("/locks/B").open("rb") as b:
        try:
            fcntl.flock(b, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            mark("DENIED_B")
            raise SystemExit(21)
        with Path("/locks/W").open("rb") as w:
            try:
                fcntl.flock(w, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                mark("DENIED_W")
                raise SystemExit(22)
            fcntl.flock(g, fcntl.LOCK_UN)
            with sqlite3.connect("/synthetic/qa.sqlite3") as db:
                db.execute("CREATE TABLE IF NOT EXISTS actors(role TEXT)")
                db.execute("INSERT INTO actors VALUES(?)", (role,))
            mark("RUNNING")
            deadline = time.monotonic() + 30
            while not stop_requested and time.monotonic() < deadline:
                time.sleep(0.05)
"""


def load_qa() -> object:
    """Use only the exact sibling LXC114 safety guard and Docker helpers."""
    spec = importlib.util.spec_from_file_location("qa_docker_g_guard", GUARD)
    if spec is None or spec.loader is None:
        raise RuntimeError("QA_GUARD_MISSING")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prepare(root: Path, qa: object) -> None:
    """Make private root-owned B/W and new G/A host-visible inodes."""
    qa.prepare(root)
    for name in ("G", "A"):
        file = root / "locks" / name
        file.touch(mode=0o640, exist_ok=False)
        os.chown(file, 0, 10001)
        os.chmod(file, 0o640)


def args(root: Path, name: str, role: str, qa: object) -> list[str]:
    """Run only nonroot networkless disposable Python + invented SQLite."""
    return (
        qa.base_flags(name)
        + qa.sandbox_mounts(root)
        + ["--entrypoint", "python", qa.IMAGE, "-c", CHILD, role]
    )


def probe_exclusive(path: Path) -> bool:
    """Try a separate host file description on the same stable inode."""
    with path.open("rb") as file:
        try:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True


def start(root: Path, name: str, role: str, qa: object) -> None:
    """Launch an exact owned offline container or fail closed."""
    result = qa.command(args(root, name, role, qa), timeout=25)
    if result.returncode:
        raise RuntimeError("QA_DOCKER_START_FAILED_" + role)


def expect(root: Path, role: str, label: str) -> None:
    """Require the exact marker from the disposable container."""
    marker = root / "events" / (role + ".status")
    deadline = time.monotonic() + 9
    while time.monotonic() < deadline:
        if marker.is_file():
            if marker.read_text() != label:
                raise RuntimeError("QA_BAD_MARKER_" + role)
            return
        time.sleep(0.05)
    raise RuntimeError("QA_MISSING_MARKER_" + role)


def stop(name: str, qa: object) -> None:
    """Stop only a container verified by its exact owner label."""
    if not qa.owner_label(name):
        raise RuntimeError("QA_CONTAINER_UNOWNED")
    signal = qa.command(["docker", "kill", "--signal=SIGTERM", name])
    if signal.returncode:
        raise RuntimeError("QA_SIGTERM_FAILED")
    deadline = time.monotonic() + 9
    while time.monotonic() < deadline:
        state = qa.command(
            [
                "docker",
                "inspect",
                "-f",
                "{{.State.Running}}",
                name,
            ]
        )
        if state.returncode == 0 and state.stdout.strip() == "false":
            return
        time.sleep(0.05)
    raise RuntimeError("QA_WRITER_DID_NOT_STOP")


def row_count(root: Path) -> int:
    """Read synthetic SQLite after a committed transaction only."""
    with closing(sqlite3.connect(root / "synthetic" / "qa.sqlite3")) as db:
        return db.execute("SELECT count(*) FROM actors").fetchone()[0]


def maintenance(root: Path, qa: object, names: tuple[str, ...]) -> None:
    """Freeze admission G EX, stop incumbent, then acquire B EX and A EX."""
    g = root / "locks/G"
    b = root / "locks/B"
    first, contender, _resumed = names
    with g.open("rb") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if probe_exclusive(b):
            raise RuntimeError("INCUMBENT_B_LOCK_NOT_HELD")
        start(root, contender, "contender", qa)
        expect(root, "contender", "DENIED_G")
        if row_count(root) != 1:
            raise RuntimeError("QA_SQLITE_WRITTEN_UNDER_G_EX")
        stop(first, qa)
        with (
            b.open("rb") as holder_b,
            (root / "locks/A").open("rb") as holder_a,
        ):
            fcntl.flock(holder_b, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(holder_a, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if row_count(root) != 1:
                raise RuntimeError("QA_DB_CHANGED_DURING_MAINTENANCE")


def run(root: Path, qa: object) -> dict:
    """Check actual in-container admission, maintenance and resumed writer."""
    token = root.name[-8:]
    names = tuple(
        "svt-qa-fence-g-" + role + "-" + token
        for role in (
            "incumbent",
            "contender",
            "resumed",
        )
    )
    try:
        start(root, names[0], "incumbent", qa)
        expect(root, "incumbent", "RUNNING")
        if probe_exclusive(root / "locks/B"):
            raise RuntimeError("INCUMBENT_NOT_FENCED")
        maintenance(root, qa, names)
        start(root, names[2], "resumed", qa)
        expect(root, "resumed", "RUNNING")
        if probe_exclusive(root / "locks/W"):
            raise RuntimeError("RESUMED_WRITER_NOT_EXCLUSIVE")
        if row_count(root) != 2:
            raise RuntimeError("SYNTHETIC_SQLITE_ROW_COUNT_WRONG")
        return {
            "result": "PASS_DOCKER_G_B_W_A_ADMISSION_QA",
            "incumbent_B_W_held": True,
            "maintenance_G_EX_while_incumbent_alive": True,
            "competing_writer_denied_G": True,
            "B_EX_A_EX_after_stop": True,
            "resumed_after_maintenance": True,
            "synthetic_sqlite_rows": 2,
            "network": "none",
            "production_changed": False,
        }
    finally:
        for name in reversed(names):
            qa.remove_owned(name)


def main() -> int:
    """Default inert with exact isolated guest and consent guard."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-disposable", action="store_true")
    parser.add_argument("--confirm", default="")
    opts = parser.parse_args()
    if not opts.run_disposable:
        print("DRY_RUN: no Docker, lock or database actions")
        return 0
    if opts.confirm != CONSENT:
        print("BLOCKED: exact LXC114 QA confirmation required")
        return 2
    try:
        qa = load_qa()
        qa.guest_guard()
        with tempfile.TemporaryDirectory(
            prefix="svt-qa-docker-gate-", dir="/var/tmp"
        ) as path:
            root = Path(path)
            prepare(root, qa)
            evidence = run(root, qa)
    except (OSError, RuntimeError) as error:
        print("QA_BLOCKED", type(error).__name__, str(error)[:210])
        return 3
    print(json.dumps(evidence, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
