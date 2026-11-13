"""Verify fail-closed Cosign selection without contacting registries."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "apply-update.sh"


class CosignSelectorTests(unittest.TestCase):
    """Use executable stand-ins inside an isolated scratch directory."""

    def setUp(self) -> None:
        """Prepare temporary fake binaries and signature evidence."""

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def fake(self, name: str, version: str) -> Path:
        """Write a fake verifier with a predictable version and receipt."""

        path = self.root / name
        path.write_text(
            "#!/bin/sh\n"
            'if [ "$1" = version ]; then\n'
            f"  printf 'GitVersion:    {version}\\n'\n"
            "  exit 0\n"
            "fi\n"
            'if [ "$1" = verify ]; then\n'
            "  printf 'verified' >> \"$COSIGN_QA_RECEIPT\"\n"
            "  exit 0\n"
            "fi\nexit 7\n",
            encoding="utf-8",
        )
        path.chmod(0o700)
        return path

    def invoke(self, command: str, *paths: Path, override: str = ""):
        """Call only shell functions, not the production updater main."""

        shell = 'source "$1"; shift; ' + command
        env = {
            **os.environ,
            "COSIGN_QA_RECEIPT": str(self.root / "receipt"),
        }
        if override:
            env["SVT_COSIGN_BIN"] = override
        else:
            env.pop("SVT_COSIGN_BIN", None)
        return subprocess.run(
            ["bash", "-c", shell, "bash", str(SCRIPT), *map(str, paths)],
            text=True,
            capture_output=True,
            env=env,
            timeout=8,
            check=False,
        )

    def test_prefer_supported_over_devel_and_use_same_binary(self):
        """Signature verification must use the selected v3 executable."""

        bad = self.fake("system", "devel")
        good = self.fake("local", "v3.0.6")
        result = self.invoke(
            'select_cosign "$1" "$2"; verify_image sha256:testing',
            bad,
            good,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "receipt").read_text(), "verified")

    def test_reject_missing_and_unsupported_verifiers(self):
        """No compatible verifier must abort without a signature request."""

        bad = self.fake("old", "v2.9.0")
        devel = self.fake("development", "devel")
        result = self.invoke('select_cosign "$1" "$2"', bad, devel)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "receipt").exists())

    def test_explicit_override_fails_closed(self):
        """An invalid explicit path cannot silently select another binary."""

        good = self.fake("local", "v3.0.6")
        result = self.invoke(
            'select_cosign "$1"', good, override="not-an-absolute-path"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "receipt").exists())


if __name__ == "__main__":
    unittest.main()
