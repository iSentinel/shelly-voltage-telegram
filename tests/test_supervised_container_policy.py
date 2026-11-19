"""Fail-closed source-only checks for the sole Docker restart owner."""

from __future__ import annotations

import argparse
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts/supervised-container.py"
)
SPEC = importlib.util.spec_from_file_location("svt_supervisor", SCRIPT)
assert SPEC and SPEC.loader
SUPERVISOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SUPERVISOR)


class SupervisedContainerPolicyTests(unittest.TestCase):
    """Reject Docker autostart, wrong mounts and pending-marker bypasses."""

    def setUp(self) -> None:
        """Build synthetic Docker inspect facts, never inspect production."""
        temp = tempfile.TemporaryDirectory(prefix="svt-supervisor-policy-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.db = self.root / "voltage.sqlite3"
        self.args = argparse.Namespace(
            container="svt-qa-disposable",
            database=self.db,
            fence_dir=self.root / "secure",
            receipt=None,
            qa_offline=True,
            once=True,
        )
        self.info = {
            "HostConfig": {
                "RestartPolicy": {"Name": "no"},
                "NetworkMode": "none",
            },
            "Mounts": [
                {
                    "Source": str(self.args.fence_dir),
                    "Destination": "/run/svt-fence",
                    "RW": True,
                },
            ],
            "Config": {"Image": "qa-offline-synthetic"},
        }

    def test_disposable_guard_accepts_single_supervisor(self) -> None:
        """One offline QA container with restart:no can be supervised."""
        SUPERVISOR.validate_container(self.info, self.args)

    def test_independent_docker_restarts_are_blocked(self) -> None:
        """Docker cannot restart the writer without a systemd owner."""
        self.info["HostConfig"]["RestartPolicy"]["Name"] = "unless-stopped"
        with self.assertRaisesRegex(
            SUPERVISOR.BootBlocked, "RESTART_OWNER_UNFENCED"
        ):
            SUPERVISOR.validate_container(self.info, self.args)

    def test_mismatched_fence_mount_is_blocked(self) -> None:
        """The host and container must share exactly the same lock inode."""
        self.info["Mounts"][0]["Source"] = str(self.root / "other")
        with self.assertRaisesRegex(
            SUPERVISOR.BootBlocked, "FENCE_MOUNT_MISMATCH"
        ):
            SUPERVISOR.validate_container(self.info, self.args)

    def test_qa_mode_rejects_network_or_nonqa_container(self) -> None:
        """Synthetic acceptance must be isolated from real devices."""
        self.info["HostConfig"]["NetworkMode"] = "bridge"
        with self.assertRaisesRegex(SUPERVISOR.BootBlocked, "NETWORK_UNSAFE"):
            SUPERVISOR.validate_container(self.info, self.args)
        self.info["HostConfig"]["NetworkMode"] = "none"
        self.args.container = "shelly-voltage-bot"
        with self.assertRaisesRegex(SUPERVISOR.BootBlocked, "QA_CONTAINER"):
            SUPERVISOR.validate_container(self.info, self.args)

    def test_signed_image_receipt_must_match(self) -> None:
        """Production candidate cannot start a substituted image."""
        self.args.qa_offline = False
        with (
            patch.object(
                SUPERVISOR, "verify_receipt", return_value="expected"
            ),
            self.assertRaisesRegex(
                SUPERVISOR.BootBlocked, "SIGNED_IMAGE_RECEIPT_MISMATCH"
            ),
        ):
            SUPERVISOR.validate_container(self.info, self.args)

    def test_dangling_symlink_pending_marker_fails_closed(self) -> None:
        """A missing symlink target is not equivalent to a clear marker."""
        marker = self.root / ".svt-update-pending"
        marker.symlink_to(self.root / "does-not-exist")
        with (
            patch.dict(os.environ, {"SVT_WRITER_FENCE_DIR": ""}),
            self.assertRaisesRegex(
                SUPERVISOR.BootBlocked, "PENDING_MARKER_UNSAFE"
            ),
        ):
            SUPERVISOR.pending(self.db)


if __name__ == "__main__":
    unittest.main()
