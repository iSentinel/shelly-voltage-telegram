"""Check QA context preparation and inventory verification offline."""

import hashlib
import json
import runpy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PREPARE = runpy.run_path(
    str(ROOT / "scripts/qa-prepare-offline-build-context.py")
)
VERIFY = runpy.run_path(str(ROOT / "scripts/qa-verify-offline-build-state.py"))
BEFORE = VERIFY["verify_before"]
AFTER = VERIFY["verify_after"]


class OfflineBuildTests(unittest.TestCase):
    """Exercise the before/after checks without installing packages."""

    def setUp(self):
        """Create a minimal synthetic staging directory."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        for name in ("runtime", "debs", "wheels"):
            (self.root / name).mkdir()
        self.file = self.root / "requirements.lock"
        self.file.write_bytes(b"lock")
        self.status = self.root / "status"
        self.status.write_bytes(b"base status")
        manifest = {"files": {"requirements.lock": self.sha(self.file)}}
        self.manifest = self.root / "input-manifest.json"
        self.manifest.write_text(json.dumps(manifest))
        self.expected = {
            "base_status_sha256": self.sha(self.status),
            "machine": "x86_64",
            "input_manifest_sha256": self.sha(self.manifest),
        }

    @staticmethod
    def sha(path):
        """Return fixture SHA256."""
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def before(self):
        """Model the exact target Python and machine."""
        with (
            patch("platform.machine", return_value="x86_64"),
            patch("platform.python_version", return_value="3.13.18"),
        ):
            BEFORE(self.root, self.expected, self.status)

    def test_before_valid(self):
        """Exact fixture inputs pass."""
        self.before()

    def test_base_drift(self):
        """Reject a changed base status."""
        self.status.write_bytes(b"other")
        with self.assertRaisesRegex(ValueError, "Base dpkg"):
            self.before()

    def test_payload_drift(self):
        """Reject modified staged bytes."""
        self.file.write_bytes(b"drift")
        with self.assertRaisesRegex(ValueError, "Staged SHA256"):
            self.before()

    def test_extra_payload(self):
        """Reject an undeclared additional package."""
        (self.root / "debs/extra.deb").write_bytes(b"extra")
        with self.assertRaisesRegex(ValueError, "file set"):
            self.before()

    def test_after_inventory(self):
        """Both installed metadata maps must match exactly."""
        expected = {"debian": {"apt": ["3", "amd64"]}, "python": {"pip": "26"}}
        functions = AFTER.__globals__
        with patch.dict(
            functions,
            {
                "installed_debian": lambda: expected["debian"],
                "installed_python": lambda: expected["python"],
            },
        ):
            AFTER(expected)
        with (
            patch.dict(
                functions,
                {
                    "installed_debian": lambda: expected["debian"],
                    "installed_python": dict,
                },
            ),
            self.assertRaisesRegex(ValueError, "Final Python"),
        ):
            AFTER(expected)

    def test_wrong_arch_archive(self):
        """Reject a digest-bound bundle claiming the wrong platform."""
        files = {f"runtime/f{i}": b"x" for i in range(51)}
        manifest = {
            "platform": "linux/arm64",
            "build_authorized": False,
            "files": {
                n: hashlib.sha256(v).hexdigest() for n, v in files.items()
            },
        }
        files["input-manifest.json"] = json.dumps(manifest).encode()
        archive = self.root / "bundle.tar"
        PREPARE["STAGE"]["write_archive"](files, archive)
        entry = {
            "archive_sha256": self.sha(archive),
            "bytes": archive.stat().st_size,
            "manifest_sha256": hashlib.sha256(
                files["input-manifest.json"]
            ).hexdigest(),
        }
        with self.assertRaisesRegex(ValueError, "platform mismatch"):
            PREPARE["load_bundle"](archive, entry, "amd64")
