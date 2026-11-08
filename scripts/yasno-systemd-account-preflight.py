"""Fail-closed YASNO systemd service-account and cache directory QA."""

from __future__ import annotations

import argparse
import pwd
import stat
import sys
from pathlib import Path


def service_account(name: str) -> tuple[int, int]:
    """Require a configured, unprivileged non-login producer identity."""

    entry = pwd.getpwnam(name)
    if entry.pw_uid in {0, 10001} or entry.pw_gid == 0:
        raise ValueError("producer account has forbidden privileges")
    if not entry.pw_shell.endswith(("/nologin", "/false")):
        raise ValueError("producer account is interactive")
    return entry.pw_uid, entry.pw_gid


def directory(path: Path, uid: int, gid: int, mode: int) -> None:
    """Enforce owner, group, mode and direct-path symlink isolation."""

    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("cache directory uses a symlink")
    if not path.is_dir():
        raise ValueError("cache directory does not exist")
    info = path.stat()
    actual = (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode))
    if actual != (uid, gid, mode):
        raise ValueError("cache directory owner/group/mode incorrect")


def file(path: Path, uid: int, gid: int, mode: int) -> None:
    """Check a regular, single-linked private cache if it exists."""

    if not path.exists():
        raise ValueError("cache file has not been provisioned")
    info = path.lstat()
    identity = (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode))
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("cache file is not a single regular file")
    if identity != (uid, gid, mode):
        raise ValueError("cache file owner/group/mode incorrect")


def verify(
    account: str,
    producer: Path,
    recipient: Path,
    reader_gid: int,
    phase: str = "live",
) -> str:
    """Check setup without files, or require complete live cache files."""

    if reader_gid < 1 or reader_gid > 65535:
        raise ValueError("reader group outside allowed range")
    if phase not in {"bootstrap", "live"}:
        raise ValueError("unsupported account verification phase")
    uid, gid = service_account(account)
    directory(producer, uid, gid, 0o700)
    directory(recipient, 0, reader_gid, 0o750)
    for path, owner, group, mode in (
        (producer / "yasno.json", uid, gid, 0o600),
        (recipient / "yasno.json", 0, reader_gid, 0o640),
    ):
        if phase == "live" or path.exists() or path.is_symlink():
            file(path, owner, group, mode)
    return (
        "producer_nologin=true reader_nonroot=true "
        f"cache_modes_ok=true phase={phase}"
    )


def main() -> int:
    """Offer safe dry-run and strict read-only prerequisite validation."""

    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--check", action="store_true")
    parser.add_argument("--account", default="shelly-yasno")
    parser.add_argument("--producer", default="/var/lib/shelly-yasno")
    parser.add_argument("--recipient", default="/var/lib/shelly-yasno-reader")
    parser.add_argument("--reader-gid", type=int, default=10001)
    parser.add_argument(
        "--phase",
        choices=("bootstrap", "live"),
        default="live",
        help="bootstrap: check directories; live: require cache files",
    )
    args = parser.parse_args()
    if args.dry_run:
        print("PASS DRY_RUN no_network=true no_files_changed=true")
        return 0
    try:
        result = verify(
            args.account,
            Path(args.producer),
            Path(args.recipient),
            args.reader_gid,
            args.phase,
        )
        print("PASS SYSTEMD_ACCOUNT", result)
        return 0
    except (ValueError, KeyError, OSError, TypeError) as exc:
        print("FAIL SYSTEMD_ACCOUNT", type(exc).__name__, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
