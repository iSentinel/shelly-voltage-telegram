"""Keep the local image-change rehearsal disposable and fail-closed."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tests/qa_offline_image_switch.sh"
CONTENT = SCRIPT.read_text(encoding="utf-8")


class QaLocalImageSwitchPolicyTests(unittest.TestCase):
    """Static source checks complement real isolated Docker acceptance."""

    def test_shell_is_valid_and_explicitly_qa_only(self) -> None:
        """Never permit this destructive fixture on a production host."""
        subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
        self.assertIn("shelly-docker-reboot-qa", CONTENT)
        self.assertIn('"$EUID" -eq 0', CONTENT)
        self.assertIn("DISCONNECTED_ROOT_QA_ONLY", CONTENT)

    def test_two_unique_local_images_without_network(self) -> None:
        """Old and new are distinct local images, not borrowed release tags."""
        self.assertIn("DISTINCT_OFFLINE_DOCKER_IMAGE_IDS_PASS", CONTENT)
        self.assertIn('[[ "$old_id" != "$new_id" ]]', CONTENT)
        self.assertIn("docker build --network none --pull=false", CONTENT)
        self.assertIn("docker create --name", CONTENT)
        self.assertIn("--restart no", CONTENT)
        self.assertIn("--network none", CONTENT)
        self.assertIn("--read-only --user 10001:10001", CONTENT)

    def test_pending_fences_both_directions(self) -> None:
        """Block writer activation before forward and reverse clearance."""
        self.assertIn("TOKEN_NEXT=qa-local-switch-forward-", CONTENT)
        self.assertIn("TOKEN_BACK=qa-local-switch-back-", CONTENT)
        self.assertEqual(CONTENT.count("SKIP_PENDING"), 2)
        self.assertIn("FORWARD_IMAGE_START_REFUSED_WHILE_PENDING", CONTENT)
        self.assertIn("REVERSE_IMAGE_START_REFUSED_WHILE_PENDING", CONTENT)
        self.assertIn('clear_marker "$TOKEN_NEXT"', CONTENT)
        self.assertIn('clear_marker "$TOKEN_BACK"', CONTENT)

    def test_crash_recovery_mode_is_explicitly_disposable(self) -> None:
        """Kill only a child coordinator, never a Docker or host process."""
        for marker in (
            "--qa-crash-recovery",
            "--qa-actor-forward",
            "--qa-actor-reverse",
            'kill -KILL "$BASHPID"',
            "QA_COORDINATOR_SIGKILL_",
            "QA_COORDINATOR_PREPARED_",
            '[[ "$rc" -ne 137 ]]',
            "FORWARD_WRONG_TOKEN_CLEAR_DENIED",
            "REVERSE_WRONG_TOKEN_CLEAR_DENIED",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, CONTENT)
        self.assertNotIn("pct reboot", CONTENT)
        self.assertNotIn("systemctl restart", CONTENT)

    def test_full_history_and_telegram_metadata_checked(self) -> None:
        """No synthetic source switch may erase pending Telegram intents."""
        for value in (
            "SELECT * FROM measurements ORDER BY id",
            "telegram_legacy_text_confirm_pending",
            "telegram_status_cleanup",
            "telegram_status_media_identity",
            "ALL_PREVIOUS_ROWS_TELEGRAM_INTENTS_AND_PHOTO_PRESERVED",
            "PRAGMA quick_check",
            "fenced-newest-rescue.py",
        ):
            with self.subTest(value=value):
                self.assertIn(value, CONTENT)

    def test_cleanup_is_limited_to_disposable_paths(self) -> None:
        """Never modify production source, image, SQLite or Telegram."""
        self.assertIn("guard_clean", CONTENT)
        self.assertIn("trap cleanup EXIT", CONTENT)
        self.assertIn("svt-qa-local-switch", CONTENT)
        self.assertNotIn("/var/lib/shelly-voltage/voltage.sqlite3", CONTENT)
        self.assertNotIn("telegram.send", CONTENT)


if __name__ == "__main__":
    unittest.main()
