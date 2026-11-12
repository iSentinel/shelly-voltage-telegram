#!/usr/bin/env python3
"""Exercise a disposable systemd restore timer without touching production."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

WRITER = "shelly-voltage-compose.service"
CONTAINER = "shelly-voltage-bot"
PREFIX = "shelly-graphic-drill"
UNIT_ROOT = Path("/run/systemd/system")


def command(argv: list[str], check: bool = True) -> str:
    """Run one bounded command with captured output."""

    result = subprocess.run(
        argv, capture_output=True, text=True, timeout=12, check=False
    )
    if check and result.returncode:
        raise RuntimeError(f"COMMAND_FAILED: {argv[0]} rc={result.returncode}")
    return result.stdout.strip()


def unit_value(unit: str, prop: str) -> str:
    """Read a selected systemd property."""

    args = ["systemctl", "show", unit, f"--property={prop}", "--value"]
    return command(args)


def production_ok() -> bool:
    """Require the original writer and container remain healthy."""

    return (
        command(["systemctl", "is-active", WRITER]) == "active"
        and command(["docker", "inspect", "--format",
                     "{{.State.Health.Status}}", CONTAINER]) == "healthy"
    )


def wait_until(test, seconds: int = 18) -> bool:
    """Poll a disposable unit with a bounded deadline."""

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if test():
            return True
        time.sleep(0.25)
    return False


def make_restore_script(path: Path, writer: str, healthy: Path) -> None:
    """Force one real restart and fail until the test health flag exists."""

    payload = (
        "#!/bin/sh\n"
        "set -eu\n"
        f"/usr/bin/systemctl restart {writer}\n"
        f"if [ ! -f {healthy} ]; then\n"
        f"  printf fail > {healthy.parent / 'failed'}\n"
        "  exit 42\n"
        "fi\n"
        f"printf pass > {healthy.parent / 'passed'}\n"
    )
    path.write_text(payload, encoding="utf-8")
    path.chmod(0o700)


def create_disposable(writer: str, watchdog: str, restore: Path) -> None:
    """Create a stopped fake writer and a timer that retries restoration."""

    unit_file = UNIT_ROOT / writer
    unit_file.write_text(
        "[Unit]\nDescription=Disposable Shelly writer test only\n"
        "[Service]\nType=oneshot\nRemainAfterExit=yes\n"
        "ExecStart=/usr/bin/true\n",
        encoding="utf-8",
    )
    unit_file.chmod(0o644)
    command(["systemctl", "daemon-reload"])
    command(["systemctl", "start", writer])
    if not wait_until(lambda: unit_value(writer, "ActiveState") == "active"):
        raise RuntimeError("DISPOSABLE_WRITER_NOT_ACTIVE")
    command(["systemctl", "stop", writer])
    if unit_value(writer, "ActiveState") != "inactive":
        raise RuntimeError("DISPOSABLE_WRITER_NOT_STOPPED")
    command([
        "systemd-run", f"--unit={watchdog}", "--on-active=2s",
        "--on-unit-active=2s", "--timer-property=AccuracySec=1s",
        "/bin/sh", str(restore),
    ])


def cleanup(writer: str, watchdog: str, work: Path) -> None:
    """Remove only the uniquely named transient test units and files."""

    for unit in (f"{watchdog}.timer", f"{watchdog}.service", writer):
        command(["systemctl", "stop", unit], check=False)
        command(["systemctl", "reset-failed", unit], check=False)
    (UNIT_ROOT / writer).unlink(missing_ok=True)
    command(["systemctl", "daemon-reload"], check=False)
    shutil.rmtree(work, ignore_errors=True)


def verify_retry(work: Path, writer: str, healthy: Path) -> dict:
    """Check the real failure marker and successful timed retry."""

    if not wait_until(lambda: (work / "failed").is_file()):
        raise RuntimeError("NO_FAILED_HEALTH_RESTORE_ATTEMPT")
    report = {"unhealthy_restore_rejected": True}
    report["writer_restarted"] = (
        unit_value(writer, "ActiveState") == "active"
    )
    healthy.touch(mode=0o600)
    if not wait_until(lambda: (work / "passed").is_file()):
        raise RuntimeError("NO_SUCCESSFUL_TIMER_RETRY")
    report["health_verified_after_retry"] = True
    report["writer_healthy"] = (
        unit_value(writer, "ActiveState") == "active"
    )
    return report


def exercise() -> dict:
    """Verify failed health gate, timer retry, and successful restoration."""

    if os.geteuid() or not production_ok():
        raise RuntimeError("PRODUCTION_BASELINE_UNSAFE")
    work = Path(tempfile.mkdtemp(prefix=f"{PREFIX}-", dir="/run"))
    stem = f"{PREFIX}-{os.getpid()}"
    writer = f"{stem}-writer.service"
    watchdog = f"{stem}-watchdog"
    healthy = work / "health-ok"
    report = {"mode": "disposable", "production_untouched": True}
    try:
        restore = work / "restore.sh"
        make_restore_script(restore, writer, healthy)
        create_disposable(writer, watchdog, restore)
        report.update(verify_retry(work, writer, healthy))
    finally:
        cleanup(writer, watchdog, work)
    report["production_healthy_after"] = production_ok()
    report["pass"] = all((
        report.get("unhealthy_restore_rejected"),
        report.get("writer_restarted"),
        report.get("health_verified_after_retry"),
        report.get("writer_healthy"),
        report["production_healthy_after"],
    ))
    return report


def main() -> int:
    """Require explicit execution of systemd-mutating disposable tests."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({"status": "DRY_RUN", "production_untouched": True}))
        return 0
    try:
        report = exercise()
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
        report = {"status": "FAIL", "error": str(error)}
    print(json.dumps(report, sort_keys=True))
    return 0 if report.get("pass") else 1


if __name__ == "__main__":
    raise SystemExit(main())
