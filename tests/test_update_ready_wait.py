"""Verify update readiness waits for fresh Shelly data, not HTTP health."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/apply-update.sh"


class UpdateReadyWaitTests(unittest.TestCase):
    """Exercise the real shell wait using offline responses."""

    def setUp(self) -> None:
        """Create fake curl and sleep commands without networking."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        curl = self.path / "curl"
        curl.write_text(
            "#!/usr/bin/env python3\n"
            "import os, sys\n"
            "from pathlib import Path\n"
            "count = Path(os.environ['READY_COUNT_FILE'])\n"
            "used = int(count.read_text()) if count.exists() else 0\n"
            "count.write_text(str(used + 1))\n"
            "if not sys.argv[-1].endswith('/ready'):\n"
            "    sys.exit(21)\n"
            "codes = os.environ['READY_CODES'].split(',')\n"
            "print(codes[min(used, len(codes) - 1)], end='')\n",
            encoding="utf-8",
        )
        curl.chmod(0o700)
        sleep = self.path / "sleep"
        sleep.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        sleep.chmod(0o700)

    def invoke(self, codes: str) -> tuple[int, int]:
        """Run only readiness function and return its exit and call counts."""
        count = self.path / "calls"
        count.unlink(missing_ok=True)
        env = {
            **os.environ,
            "PATH": str(self.path) + os.pathsep + os.environ["PATH"],
            "READY_CODES": codes,
            "READY_COUNT_FILE": str(count),
        }
        result = subprocess.run(
            ["bash", "-c", 'source "$1"; wait_healthy', "bash", str(SCRIPT)],
            capture_output=True,
            text=True,
            env=env,
            timeout=10,
            check=False,
        )
        return result.returncode, int(count.read_text())

    def test_waits_for_fresh_sample_after_http_health(self) -> None:
        """Initial 503 status must not abort an eventually ready image."""
        self.assertEqual(self.invoke("503,503,200,200,200"), (0, 5))

    def test_transient_503_resets_consecutive_success_count(self) -> None:
        """A failed ready probe resets the verified stability window."""
        self.assertEqual(self.invoke("200,200,503,200,200,200"), (0, 6))

    def test_never_ready_fails_closed(self) -> None:
        """One minute of fixture attempts must not force an unsafe update."""
        self.assertEqual(self.invoke("503"), (1, 60))

    def test_interleaved_200_and_503_still_fails_closed(self) -> None:
        """Flapping /ready must never be accepted as stable."""
        self.assertEqual(self.invoke("200,503"), (1, 60))


if __name__ == "__main__":
    unittest.main()
