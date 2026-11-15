#!/usr/bin/env python3
"""Test real transient systemd ExecStartPre on an offline journal model."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "scripts/disposable-status-journal-recovery-qa.py"
CONFIRM = "OFFLINE_JOURNAL_SYSTEMD_PRESTART_QA_ONLY"
CASES = ("published_pending", "published_missing", "committed", "corrupt")

PROBE = """
import importlib.util
import sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("qa_journal", sys.argv[1])
qa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qa)
folder = Path(sys.argv[2])
status = qa.classify(folder, *qa.trusted())
print("QA_PRESTART_STATUS", status, flush=True)
if status != "COMMITTED":
    raise SystemExit(32)
if sys.argv[3] == "start":
    (folder / "started.flag").write_text("qa-start-allowed")
"""


def load_model() -> object:
    """Import only the fixed local QA module, never production source."""
    spec = importlib.util.spec_from_file_location("journal_synthetic", MODEL)
    if spec is None or spec.loader is None:
        raise RuntimeError("JOURNAL_MODULE_MISSING")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prepare(qa: object, folder: Path, scenario: str) -> None:
    """Make a synthetic release state for an ExecStartPre check."""
    qa.initialize(folder)
    for phase in ("private_written", "public_written", "published"):
        qa.checkpoint(folder, phase)
    if scenario == "published_missing":
        (folder / "pending-update").unlink()
        qa.sync_dir(folder)
    elif scenario in ("committed", "corrupt"):
        if qa.reconcile(folder, *qa.trusted()) != "COMMITTED":
            raise RuntimeError("UNEXPECTED_QA_COMMIT_FAILURE")
    if scenario == "corrupt":
        (folder / "public.json").write_text("{invalid")


def run_unit(folder: Path, scenario: str) -> subprocess.CompletedProcess:
    """Run real disposable systemd prestart with no network or prod paths."""
    probe = folder / "prestart-probe.py"
    probe.write_text(PROBE)
    unit = "svt-qa-journal-prestart-" + str(os.getpid()) + "-" + scenario
    cmd = [
        "systemd-run",
        "--unit=" + unit,
        "--wait",
        "--pipe",
        "--collect",
        "-p",
        "Type=oneshot",
        "-p",
        "PrivateNetwork=yes",
        "-p",
        "ProtectSystem=strict",
        "-p",
        "ReadWritePaths=" + str(folder),
        "-p",
        (
            "ExecStartPre=/usr/bin/python3 "
            + str(probe)
            + " "
            + str(MODEL)
            + " "
            + str(folder)
            + " check"
        ),
        "/usr/bin/python3",
        str(probe),
        str(MODEL),
        str(folder),
        "start",
    ]
    return subprocess.run(
        cmd, check=False, capture_output=True, text=True, timeout=35
    )


def run_case(qa: object, scenario: str) -> dict:
    """Require blocked prestart or successful mark for exactly one state."""
    with tempfile.TemporaryDirectory(
        prefix="svt-journal-systemd-", dir="/var/tmp"
    ) as name:
        folder = Path(name)
        prepare(qa, folder, scenario)
        result = run_unit(folder, scenario)
        allowed = scenario == "committed"
        marked = (folder / "started.flag").exists()
        if (result.returncode == 0) != allowed or marked != allowed:
            raise RuntimeError(
                "SYSTEMD_START_MISMATCH_"
                + scenario
                + "_RC_"
                + str(result.returncode)
                + "_OUTPUT_"
                + (result.stdout + result.stderr)[-300:]
            )
        return {
            "scenario": scenario,
            "systemd_rc": result.returncode,
            "writer_start_marker": marked,
            "expected_allowed": allowed,
        }


def main() -> int:
    """Default no-op; require exact QA guest and authorization to start."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-disposable", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    if not args.run_disposable:
        print("DRY_RUN: no transient unit launched")
        return 0
    if args.confirm != CONFIRM:
        print("BLOCKED: exact QA consent required")
        return 2
    try:
        qa = load_model()
        qa.guest_guard()
        results = [run_case(qa, name) for name in CASES]
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print("BLOCKED", type(exc).__name__, str(exc)[:350])
        return 3

    print(
        json.dumps(
            {
                "status": "PASS_REAL_SYSTEMD_JOURNAL_PRESTART",
                "cases": results,
                "trust": "MOCKED_QA",
                "production_modified": False,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
