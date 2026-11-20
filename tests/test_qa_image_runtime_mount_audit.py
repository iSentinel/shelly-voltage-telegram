"""Fail-closed tests for the non-authorizing Docker runtime mount audit."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts"
SPEC = importlib.util.spec_from_file_location(
    "svt_qa_mount_audit", SCRIPT / "qa-image-runtime-mount-audit.py"
)
assert SPEC is not None and SPEC.loader is not None
MOUNT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOUNT)


def fixture() -> list[dict]:
    """Build a synthetic non-root code image with required data mounts."""
    result = []
    for destination, (source, writable) in MOUNT.MANDATORY.items():
        result.append(
            {
                "Type": "bind",
                "Source": source,
                "Destination": destination,
                "RW": writable,
            }
        )
    result.append(
        {
            "Type": "bind",
            "Source": "/opt/shelly-voltage/secrets/telegram_token",
            "Destination": "/run/secrets/telegram_token",
            "RW": False,
        }
    )
    return result


class MountAuditTests(unittest.TestCase):
    """Require a self-contained image layout without trusting source bytes."""

    def test_known_layout_is_evidence_only(self) -> None:
        """A layout match never authorizes signing or rollback."""
        result = MOUNT.check_mounts(fixture())
        self.assertEqual(
            result["status"], "PASS_RUNTIME_MOUNT_LAYOUT_EVIDENCE_ONLY"
        )
        self.assertFalse(result["old_oci_signature_verified"])
        self.assertFalse(result["mount_source_path_trust_verified"])
        self.assertFalse(result["rollback_authorized"])
        self.assertFalse(result["release_authorized"])

    def test_application_code_bind_is_denied(self) -> None:
        """Never let a host directory override self-contained code."""
        mounts = fixture()
        mounts.append(
            {
                "Type": "bind",
                "Source": "/opt/shelly-voltage",
                "Destination": "/app",
                "RW": False,
            }
        )
        with self.assertRaisesRegex(
            MOUNT.MountBlocked, "MOUNT_DESTINATION_NOT_ALLOWLISTED"
        ):
            MOUNT.check_mounts(mounts)

    def test_writable_secret_is_denied(self) -> None:
        """Tokens must never become writable via bind configuration."""
        mounts = fixture()
        mounts[-1]["RW"] = True
        with self.assertRaisesRegex(
            MOUNT.MountBlocked, "MOUNT_SOURCE_OR_MODE_MISMATCH"
        ):
            MOUNT.check_mounts(mounts)

    def test_wrong_database_source_is_denied(self) -> None:
        """An alternate volume is not automatically continuous history."""
        mounts = fixture()
        mounts[0]["Source"] = "/var/backups/shelly-voltage"
        with self.assertRaisesRegex(
            MOUNT.MountBlocked, "MOUNT_SOURCE_OR_MODE_MISMATCH"
        ):
            MOUNT.check_mounts(mounts)

    def test_missing_required_data_mount_is_denied(self) -> None:
        """A rootless-looking image without a real DB source still fails."""
        mounts = fixture()[1:]
        with self.assertRaisesRegex(
            MOUNT.MountBlocked, "MOUNT_REQUIRED_DATA_SOURCE_MISSING"
        ):
            MOUNT.check_mounts(mounts)

    def test_duplicate_secret_destination_is_denied(self) -> None:
        """Two source paths must not race to populate the same target."""
        mounts = fixture()
        mounts.append(mounts[-1].copy())
        with self.assertRaisesRegex(
            MOUNT.MountBlocked, "MOUNT_DESTINATION_DUPLICATE_OR_INVALID"
        ):
            MOUNT.check_mounts(mounts)

    def test_oversized_inventory_is_denied(self) -> None:
        """Refuse oversized or nonregular user-provided inventories."""
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "mounts.json"
            path.write_text("x" * (128 * 1024 + 1), encoding="ascii")
            with self.assertRaisesRegex(
                MOUNT.MountBlocked, "MOUNT_INVENTORY_FILE_UNSAFE"
            ):
                MOUNT.read_mounts(path)

    def test_symlinked_inventory_is_denied(self) -> None:
        """A symlink cannot substitute untrusted mount metadata."""
        with tempfile.TemporaryDirectory() as temp:
            real = Path(temp) / "original.json"
            alias = Path(temp) / "alias.json"
            real.write_text(json.dumps(fixture()), encoding="utf-8")
            alias.symlink_to(real)
            with self.assertRaisesRegex(
                MOUNT.MountBlocked, "MOUNT_INVENTORY_PATH_INVALID"
            ):
                MOUNT.read_mounts(alias)

    def test_fixture_inventory_is_parseable(self) -> None:
        """Read only an explicit small inventory, not a full Docker dump."""
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "mounts.json"
            path.write_text(json.dumps(fixture()), encoding="utf-8")
            self.assertEqual(len(MOUNT.read_mounts(path)), 4)


if __name__ == "__main__":
    unittest.main()
