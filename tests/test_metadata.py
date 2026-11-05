import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def is_public_text_file(path: Path) -> bool:
    """Select public text files while excluding private runtime files."""

    private_dirs = {
        ".git",
        ".ruff_cache",
        "__pycache__",
        "validation",
        "secrets",
        "dist",
        "dist-audit",
        "dist-publish",
    }
    if any(part in private_dirs for part in path.parts):
        return False
    if path.name == ".env" or (
        path.name.startswith(".env.") and path.name != ".env.example"
    ):
        return False
    return path.is_file() and path.suffix.lower() not in {
        ".png",
        ".jpg",
        ".jpeg",
        ".gz",
    }


class MetadataConsistencyTests(unittest.TestCase):
    def test_public_metadata_matches_version(self):
        version = (ROOT / "VERSION").read_text().strip()
        release_version = (ROOT / "RELEASE_VERSION").read_text().strip()
        live_version = (ROOT / "LIVE_VERSION").read_text().strip()
        readme = (ROOT / "README.md").read_text()
        changelog = (ROOT / "CHANGELOG.md").read_text()
        hardware = (ROOT / "HARDWARE_VALIDATION.md").read_text()
        self.assertIn(f"Current release: **{release_version}**", readme)
        if version != release_version:
            self.assertIn(f"Current software candidate: **{version}**", readme)
        self.assertIn(f"## {version} -", changelog)
        self.assertIn(
            f"Hardware-accepted live checkpoint: **{live_version}**", readme
        )
        self.assertIn(f"Checkpoint: `{live_version}`", hardware)

    def test_changelog_versions_are_unique_and_descending(self):
        """Keep release history ordered and free of duplicate versions."""
        text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        matches = re.findall(r"^## (\d+)\.(\d+)\.(\d+)-hw", text, re.M)
        versions = [tuple(map(int, row)) for row in matches]
        self.assertEqual(len(versions), len(set(versions)))
        self.assertEqual(versions, sorted(versions, reverse=True))

    def test_public_docs_omit_private_device_identifiers(self):
        docs = (
            "README.md",
            "ROADMAP.md",
            "HARDWARE_VALIDATION.md",
            "CHANGELOG.md",
        )
        private_ipv4 = re.compile(r"\b192\.168\.\d{1,3}\.\d{1,3}\b")
        mac = re.compile(r"\b(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\b")
        for name in docs:
            text = (ROOT / name).read_text()
            self.assertIsNone(private_ipv4.search(text), name)
            self.assertIsNone(mac.search(text), name)

    def test_private_env_backups_are_excluded(self):
        """Never read sensitive local configuration backup files."""

        for name in (".env", ".env.pre-ip-fix-20261104", ".env.bak"):
            self.assertFalse(is_public_text_file(ROOT / name))
        self.assertTrue(is_public_text_file(ROOT / ".env.example"))

    def test_public_tree_has_no_deployment_specific_ids(self):
        deployment_values = {}
        env_path = ROOT / ".env"
        if env_path.exists() and env_path.stat().st_mode & 0o004:
            for line in env_path.read_text().splitlines():
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                if (
                    key
                    in {
                        "SHELLY_HOST",
                        "TELEGRAM_STATUS_CHAT_ID",
                        "TELEGRAM_ADMIN_USER_IDS",
                    }
                    and value
                ):
                    deployment_values[key] = value
        for path in ROOT.rglob("*"):
            if not is_public_text_file(path):
                continue
            text = path.read_text(errors="ignore")
            for label, value in deployment_values.items():
                self.assertNotIn(
                    value, text, f"deployment value {label}: {path}"
                )


if __name__ == "__main__":
    unittest.main()
