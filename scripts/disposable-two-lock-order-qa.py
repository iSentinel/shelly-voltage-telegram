#!/usr/bin/env python3
"""Prove updater and supervisor lock-order hazards in private scratch."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

CONFIRM = "DISPOSABLE_TWO_LOCK_ORDER_ONLY"
CHILD = """
import fcntl,os,sys,time
from pathlib import Path
first,second,mode,root,name=sys.argv[1:]
fd=os.open(first,os.O_RDONLY|os.O_CLOEXEC)
fcntl.flock(fd,fcntl.LOCK_SH if mode=='sh' else fcntl.LOCK_EX)
Path(root,name+'.ready').write_text('READY')
end=time.monotonic()+4
while not Path(root,('b' if name=='a' else 'a')+'.ready').exists():
 if time.monotonic()>end:
  sys.exit(7)
 time.sleep(.01)
second_fd=os.open(second,os.O_RDONLY|os.O_CLOEXEC)
outcome='SECOND_ACQUIRED'
try:
 fcntl.flock(second_fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
except BlockingIOError:
 outcome='SECOND_BLOCKED'
Path(root,name+'.probed').write_text(outcome)
end=time.monotonic()+4
while not Path(root,('b' if name=='a' else 'a')+'.probed').exists():
 if time.monotonic()>end:
  sys.exit(8)
 time.sleep(.01)
print(outcome)
sys.exit(3 if outcome=='SECOND_BLOCKED' else 0)
"""


def require(condition: bool, text: str) -> None:
    """Raise an explicit QA failure instead of a false successful test."""
    if not condition:
        raise RuntimeError(text)


def create_lock(path: Path) -> None:
    """Create one private fresh regular lock inode with no symlink."""
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    try:
        metadata = os.fstat(fd)
        require(
            stat.S_ISREG(metadata.st_mode)
            and stat.S_IMODE(metadata.st_mode) == 0o600,
            "PRIVATE_LOCK_UNTRUSTED",
        )
    finally:
        os.close(fd)


def probe(path: Path, mode: str) -> str:
    """Try a lock in another process without indefinite waiting."""
    code = (
        "import fcntl,os,sys;"
        "fd=os.open(sys.argv[1],os.O_RDONLY);"
        "mode=fcntl.LOCK_EX if sys.argv[2]=='ex' else fcntl.LOCK_SH;"
        "result='ACQUIRED';"
        "\ntry: fcntl.flock(fd,mode|fcntl.LOCK_NB)"
        "\nexcept BlockingIOError: result='BLOCKED'"
        "\nprint(result)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(path), mode],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    require(result.returncode == 0, "CHILD_PROBE_FAILURE")
    require(result.stdout.strip() in {"ACQUIRED", "BLOCKED"}, "BAD_PROBE")
    return result.stdout.strip()


def split_lock_cases(maintenance: Path, updater: Path) -> dict:
    """Exercise independent maintenance and updater mutex semantics."""
    with maintenance.open("rb") as owner:
        fcntl.flock(owner, fcntl.LOCK_SH)
        same = probe(maintenance, "ex")
        distinct = probe(updater, "ex")
    with maintenance.open("rb") as owner:
        fcntl.flock(owner, fcntl.LOCK_EX)
        excluded = probe(maintenance, "sh")
    with maintenance.open("rb") as owner, updater.open("rb") as mutex:
        fcntl.flock(owner, fcntl.LOCK_EX)
        fcntl.flock(mutex, fcntl.LOCK_EX)
        ordered = probe(maintenance, "sh")
    require(
        (same, distinct, excluded, ordered)
        == ("BLOCKED", "ACQUIRED", "BLOCKED", "BLOCKED"),
        "SPLIT_LOCK_PROTOCOL_UNEXPECTED",
    )
    return {
        "same_inode_prestart": same,
        "separate_updater_prestart": distinct,
        "updater_excludes_supervisor": excluded,
        "updater_acquires_maintenance_then_mutex": ordered,
    }


def launch_cycle_holder(
    first: Path, second: Path, mode: str, root: Path, name: str
) -> subprocess.Popen:
    """Start a bounded child that holds one lock and probes a second."""
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            CHILD,
            str(first),
            str(second),
            mode,
            str(root),
            name,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def circular_wait(root: Path, maintenance: Path, updater: Path) -> dict:
    """Prove a cross-lock cycle with two independent child holders."""
    processes = []
    try:
        a = launch_cycle_holder(updater, maintenance, "ex", root, "a")
        processes.append(a)
        b = launch_cycle_holder(maintenance, updater, "sh", root, "b")
        processes.append(b)
        first = a.communicate(timeout=8)
        second = b.communicate(timeout=8)
        require(
            a.returncode == b.returncode == 3
            and first[0].strip() == second[0].strip() == "SECOND_BLOCKED",
            (
                "CIRCULAR_WAIT_NOT_REPRODUCED "
                f"a={a.returncode}:{first} b={b.returncode}:{second}"
            ),
        )
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
    return {
        "first_process": "SECOND_BLOCKED",
        "second_process": "SECOND_BLOCKED",
    }


def run_disposable() -> dict:
    """Use only two created scratch lockfiles, never production paths."""
    with tempfile.TemporaryDirectory(
        prefix="svt-private-lock-order-", dir="/var/tmp"
    ) as directory:
        root = Path(directory)
        root.chmod(0o700)
        maintenance = root / "maintenance.lock"
        updater = root / "updater.lock"
        create_lock(maintenance)
        create_lock(updater)
        require(
            maintenance.stat().st_ino != updater.stat().st_ino,
            "UPDATER_MUTEX_MUST_HAVE_SEPARATE_INODE",
        )
        split = split_lock_cases(maintenance, updater)
        cycle = circular_wait(root, maintenance, updater)
    return {
        "status": "PASS_PRIVATE_TWO_LOCK_ORDER_QA",
        "split": split,
        "reversed_order_cycle": cycle,
        "production_modified": False,
        "docker_or_systemd_started": False,
        "production_lock_files_opened": False,
        "boot_start_authorized": False,
        "source_install_authorized": False,
        "telegram_photo_authorized": False,
    }


def main() -> int:
    """Default to a no-op; require exact disposable approval to run."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--run-disposable", action="store_true")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--jsonl", action="store_true")
    args = parser.parse_args()
    if args.dry_run or not args.run_disposable:
        print("DRY-RUN PASS: no locks, Docker or systemd touched")
        return 0
    if args.confirm != CONFIRM:
        print("FAIL: disposable-only exact confirmation required")
        return 2
    try:
        result = run_disposable()
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print("FAIL", type(error).__name__, str(error))
        return 3
    print(json.dumps(result) if args.jsonl else result["status"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
