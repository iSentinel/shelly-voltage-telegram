"""Check real Linux UID separation without network or production writes."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from test_yasno_schedule import response

from yasno_cache import atomic_write, build_cache

ROOT = Path(__file__).resolve().parents[1]
KYIV = ZoneInfo("Europe/Kyiv")
PRODUCER_ID = 10002
READER_ID = 10001


def run_as(args: list[str], identity: int | None = None) -> str:
    """Run one bounded subprocess, returning its non-secret PASS text."""

    prefix = ["setpriv", "--no-new-privs"]
    if identity is not None:
        prefix.extend(
            [
                "--reuid",
                str(identity),
                "--regid",
                str(identity),
                "--clear-groups",
            ]
        )
    else:
        prefix.append("--bounding-set=-all,+chown,+dac_override")
    result = subprocess.run(
        [*prefix, sys.executable, *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if result.returncode:
        raise AssertionError(
            f"QA subprocess failed: exit={result.returncode} "
            f"stderr={result.stderr[-350:]}"
        )
    return result.stdout.strip()


def make_fixture(tmp: str) -> tuple[Path, Path]:
    """Create a synthetic sealed source and read-only recipient folder."""

    base = Path(tmp)
    base.chmod(0o711)
    producer = base / "producer"
    reader = base / "reader"
    producer.mkdir(mode=0o700)
    reader.mkdir(mode=0o750)
    os.chown(producer, PRODUCER_ID, PRODUCER_ID)
    os.chown(reader, 0, READER_ID)
    source = producer / "yasno.json"
    captured = datetime.now(KYIV) - timedelta(seconds=60)
    atomic_write(
        str(source), build_cache(response(datetime.now(KYIV)), captured)
    )
    os.chown(source, PRODUCER_ID, PRODUCER_ID)
    return source, reader / "yasno.json"


def consumer_script() -> str:
    """Return a tiny recipient-side access check with no networking."""

    return (
        "import os;from pathlib import Path;"
        "source=Path(__import__('sys').argv[1]);"
        "target=Path(__import__('sys').argv[2]);"
        "assert target.read_text(encoding='utf-8').find('ScheduleApplies')>=0;"
        "assert not os.access(target,os.W_OK);"
        "assert not os.access(target.parent,os.W_OK);"
        "assert not os.access(source,os.R_OK);"
        "print('PASS UID10001 isolated recipient read-only')"
    )


@unittest.skipUnless(
    os.geteuid() == 0 and shutil.which("setpriv"),
    "Requires Linux root + setpriv in an isolated QA environment",
)
class YasnoRoleIsolationAcceptanceTests(unittest.TestCase):
    """Exercise actual producer, exporter and recipient Linux identities."""

    def test_handoff_from_different_owner_to_nonroot_reader(self):
        """Read sealed data without granting the consumer source access."""

        with tempfile.TemporaryDirectory(
            prefix="shelly-yasno-role-qa-", dir="/tmp"
        ) as tmp:
            source, target = make_fixture(tmp)
            refresh = [
                str(ROOT / "scripts/yasno-refresh.py"),
                "--once",
                "--cache",
                str(source),
                "--group",
                "10.1",
            ]
            self.assertIn("SKIPPED", run_as(refresh, PRODUCER_ID))
            exporter = [
                str(ROOT / "yasno_handoff.py"),
                "--install",
                "--source",
                str(source),
                "--output",
                str(target),
                "--recipient-gid",
                str(READER_ID),
                "--group",
                "10.1",
            ]
            self.assertIn("UPDATED", run_as(exporter))
            result = run_as(
                ["-c", consumer_script(), str(source), str(target)],
                READER_ID,
            )
            self.assertIn("PASS UID10001", result)
            self.assertEqual(target.stat().st_gid, READER_ID)
            self.assertEqual(target.stat().st_mode & 0o777, 0o640)


if __name__ == "__main__":
    unittest.main()
