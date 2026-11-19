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
            source_version=None,
            qa_offline=True,
            once=True,
        )
        self.info = {
            "State": {"Running": False},
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

    def test_foreign_running_writer_refused(self) -> None:
        """Systemd must not silently adopt an existing live Docker writer."""
        self.info["State"]["Running"] = True
        with self.assertRaisesRegex(
            SUPERVISOR.BootBlocked, "OWNER_UNVERIFIED"
        ):
            SUPERVISOR.validate_container(self.info, self.args)

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

    def test_version_pair_requires_exact_match(self) -> None:
        """A valid digest cannot authorize a different source release."""
        record = {"version": "0.5.25-hw", "digest": "sha256:" + "a" * 64}
        SUPERVISOR.require_version_pair(record, "0.5.25-hw")
        with self.assertRaisesRegex(
            SUPERVISOR.BootBlocked, "SOURCE_SIGNED_VERSION_MISMATCH"
        ):
            SUPERVISOR.require_version_pair(record, "0.5.24-hw")

    def test_version_pair_refuses_unknown_and_mutable_versions(self) -> None:
        """No missing, mutable or malformed source/receipt versions."""
        for version in (None, "latest", "0.5.25", "0.5.25-hw+dirty"):
            with (
                self.subTest(version=version),
                self.assertRaisesRegex(
                    SUPERVISOR.BootBlocked, "RECEIPT_VERSION_INVALID"
                ),
            ):
                SUPERVISOR.require_version_pair(
                    {"version": version}, "0.5.25-hw"
                )

    def test_missing_source_version_file_fails_closed(self) -> None:
        """The runtime cannot invent a version for a missing source file."""
        with self.assertRaisesRegex(
            SUPERVISOR.BootBlocked, "SOURCE_VERSION_PATH_INVALID"
        ):
            SUPERVISOR.source_version(None)

    def test_linked_source_version_refused(self) -> None:
        """A symbolic source marker cannot be used as a signed identity."""
        target = self.root / "release-version.txt"
        target.write_text("0.5.25-hw\n")
        linked = self.root / "RELEASE_VERSION"
        linked.symlink_to(target)
        with self.assertRaisesRegex(
            SUPERVISOR.BootBlocked, "SOURCE_VERSION_PATH_UNSAFE"
        ):
            SUPERVISOR.source_version(linked)

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
