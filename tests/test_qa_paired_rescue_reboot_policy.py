"""Guard the isolated reboot rescue fixture and Telegram intent parity."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tests/qa_paired_rescue_reboot.sh"
SOURCE = SCRIPT.read_text(encoding="utf-8")


class QaPairedRescueRebootPolicyTests(unittest.TestCase):
    """Keep fault/reboot acceptance disconnected and non-authorizing."""

    def test_syntax_and_qa_root_host_guard(self) -> None:
        """Reject a real host and invalid invocation before mutation."""
        subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
        self.assertIn("shelly-docker-reboot-qa", SOURCE)
        self.assertIn('"$EUID" -eq 0', SOURCE)
        self.assertIn("LXC114 root-only QA", SOURCE)
        self.assertNotIn("/var/lib/shelly-voltage/voltage.sqlite3", SOURCE)

    def test_actual_boot_id_change_required_in_both_modes(self) -> None:
        """A no-op restart cannot pretend to prove guest reboot."""
        self.assertEqual(SOURCE.count('boot-id-before"'), 6)
        self.assertIn("guest boot ID unchanged", SOURCE)
        self.assertIn("fault-boundary guest boot ID unchanged", SOURCE)
        self.assertIn("prepare-fault", SOURCE)
        self.assertIn("verify-fault", SOURCE)

    def test_rescue_preserves_telegram_pending_and_cleanup(self) -> None:
        """Require both unconfirmed sends and scheduled cleanup rows."""
        for item in (
            "telegram_legacy_text_confirm_pending",
            "telegram_status_cleanup",
            "telegram_status_media_identity",
            "telegram_status_message_id",
            "store.reserve_text_post()",
            "needs_unpin,retry_at",
        ):
            with self.subTest(item=item):
                self.assertIn(item, SOURCE)

    def test_pending_does_not_clear_before_receipt_recovery(self) -> None:
        """Demand a durable full rescue before resuming the writer."""
        for item in (
            "stage-guarded-update.py",
            "--recover-receipt",
            "FAULT_RECEIPT_RECONSTRUCTION_AND_PAIR_PARITY_PASS",
            "PENDING_RESCUE_SURVIVED_GUEST_REBOOT_PASS",
            "POST_REBOOT_NEW_THREE_PHASE_CYCLES_PASS",
            "--qa-offline --once",
        ):
            with self.subTest(item=item):
                self.assertIn(item, SOURCE)
        self.assertIn("--restart no --network none", SOURCE)
        self.assertIn("--read-only --user 10001:10001", SOURCE)


if __name__ == "__main__":
    unittest.main()
