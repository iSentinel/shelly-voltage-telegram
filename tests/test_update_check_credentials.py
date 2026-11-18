"""Verify the systemd DynamicUser checker has no private-source dependency."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "systemd/shelly-voltage-update-check.service"
SOURCE = ROOT / "scripts/github_update.py"


class UpdateCheckCredentialsTests(unittest.TestCase):
    """Test only synthetic credential files and local status JSON."""

    def setUp(self) -> None:
        """Construct read-only public credential copies without any secrets."""

        self.temp = tempfile.TemporaryDirectory(prefix="svt-checker-qa-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.credentials = self.root / "credentials"
        self.state = self.root / "state"
        self.credentials.mkdir()
        self.state.mkdir()
        shutil.copyfile(SOURCE, self.credentials / "github_update.py")
        (self.credentials / "RELEASE_VERSION").write_text("0.5.25-hw\n")
        spec = importlib.util.spec_from_file_location(
            "qa_github_update_credential",
            self.credentials / "github_update.py",
        )
        assert spec is not None and spec.loader is not None
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def service(self) -> str:
        """Return exactly the source-controlled service unit."""

        return SERVICE.read_text(encoding="utf-8")

    def test_dynamic_user_has_no_project_working_directory(self) -> None:
        """Never rely on traversal of a protected 0750 project root."""

        s = self.service()
        self.assertIn("DynamicUser=yes", s)
        self.assertIn("WorkingDirectory=/\n", s)
        self.assertNotIn("WorkingDirectory=/opt/shelly-voltage", s)
        self.assertNotIn("SupplementaryGroups=", s)

    def test_exact_two_public_credentials_only(self) -> None:
        """Copy only public checker code and installed release marker."""

        values = [
            x
            for x in self.service().splitlines()
            if x.startswith("LoadCredential=")
        ]
        self.assertEqual(
            values,
            [
                "LoadCredential=github_update.py:"
                "/opt/shelly-voltage/scripts/github_update.py",
                "LoadCredential=RELEASE_VERSION:"
                "/opt/shelly-voltage/RELEASE_VERSION",
            ],
        )
        self.assertFalse(
            any("secrets/" in line or "/.env" in line for line in values)
        )

    def test_execute_only_credential_code_and_version(self) -> None:
        """No direct project source access is needed by the checker UID."""

        s = self.service()
        self.assertIn(
            "ExecStart=/usr/bin/python3 %d/github_update.py check --root %d", s
        )
        self.assertNotIn("ExecStart=/bin/bash", s)
        self.assertIn("NoNewPrivileges=yes", s)
        self.assertIn("ProtectSystem=strict", s)
        self.assertIn("CapabilityBoundingSet=\n", s)

    def test_current_release_is_not_an_update(self) -> None:
        """Synthetic release inspection never invokes Docker or Telegram."""

        with patch.object(
            self.module,
            "published_release",
            return_value={"tag_name": "v0.5.25-hw"},
        ):
            status = self.module.check(self.credentials, self.state)
        self.assertFalse(status["available"])
        self.assertFalse(status["auto_install"])
        self.assertEqual(status["installed"], "0.5.25-hw")
        self.assertEqual(status["latest"], "0.5.25-hw")

    def test_newer_release_only_flags_availability(self) -> None:
        """Even a newer version cannot launch automatic deployment."""

        with patch.object(
            self.module,
            "published_release",
            return_value={"tag_name": "v0.5.26-hw"},
        ):
            result = self.module.check(self.credentials, self.state)
        self.assertTrue(result["available"])
        self.assertFalse(result["auto_install"])
        self.assertEqual(result["latest"], "0.5.26-hw")

    def test_atomic_status_write_is_confined_to_state_directory(self) -> None:
        """The only fixture mutation is a private checker status file."""

        with patch.object(
            self.module,
            "published_release",
            return_value={"tag_name": "v0.5.25-hw"},
        ):
            result = self.module.check(self.credentials, self.state)
        output = self.state / "status.json"
        self.module.atomic_json(output, result)
        saved = json.loads(output.read_text())
        self.assertEqual(saved["installed"], "0.5.25-hw")
        self.assertFalse(saved["auto_install"])
        self.assertFalse((self.credentials / "status.json").exists())

    def test_missing_credential_marker_blocks_check(self) -> None:
        """Fail when PID1 did not provide the required release marker."""

        (self.credentials / "RELEASE_VERSION").unlink()
        with self.assertRaises(OSError):
            self.module.installed_version(self.credentials, self.state)

    def test_credential_script_help_works_without_network(self) -> None:
        """The copied script needs no project directory or root token."""

        result = subprocess.run(
            [
                sys.executable,
                str(self.credentials / "github_update.py"),
                "--help",
            ],
            capture_output=True,
            text=True,
            timeout=12,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("check", result.stdout)
        self.assertIn("--root", result.stdout)


if __name__ == "__main__":
    unittest.main()
