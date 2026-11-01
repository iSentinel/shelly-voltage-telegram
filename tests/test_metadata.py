import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class MetadataConsistencyTests(unittest.TestCase):
    def test_public_metadata_matches_version(self):
        version = (ROOT / "VERSION").read_text().strip()
        live_version = (ROOT / "LIVE_VERSION").read_text().strip()
        readme = (ROOT / "README.md").read_text()
        changelog = (ROOT / "CHANGELOG.md").read_text()
        hardware = (ROOT / "HARDWARE_VALIDATION.md").read_text()
        if version == live_version:
            self.assertIn(f"Current release: **{version}**", readme)
        else:
            self.assertIn(f"Current software candidate: **{version}**", readme)
        self.assertIn(f"## {version} -", changelog)
        self.assertIn(
            f"Hardware-accepted live checkpoint: **{live_version}**", readme
        )
        self.assertIn(f"Checkpoint: `{live_version}`", hardware)

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

    def test_public_tree_has_no_deployment_specific_ids(self):
        excluded = {".env", "validation", "secrets", "dist", "dist-publish"}
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
            if not path.is_file() or any(
                part in excluded for part in path.parts
            ):
                continue
            if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gz"}:
                continue
            text = path.read_text(errors="ignore")
            for label, value in deployment_values.items():
                self.assertNotIn(
                    value, text, f"deployment value {label}: {path}"
                )


if __name__ == "__main__":
    unittest.main()
