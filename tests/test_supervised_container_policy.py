"""Fail-closed source-only checks for the sole Docker restart owner."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import stat
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

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
            require_pair_receipt=False,
            source_archive_dir=None,
            qa_offline=True,
            once=True,
        )
        self.identity = "a" * 64
        self.info = {
            "Id": self.identity,
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

    def pair_record(self) -> dict:
        """Build a synthetic cryptographic pair receipt for policy tests."""
        return {
            "version": "0.5.26-hw",
            "digest": "sha256:" + "b" * 64,
            "source_commit": "a" * 40,
            "source_sha256": "c" * 64,
            "transaction_token": "qa-supervisor-pair-test",
            "pair_status": "PASS_SIGNED_SOURCE_OCI_PAIR_ONLY",
            "source_signature_verified": True,
            "oci_signature_verified": True,
            "oci_attestations_verified": True,
        }

    def test_paired_receipt_binds_exact_image_revision(self) -> None:
        """The candidate refuses an image built from another source SHA."""
        SUPERVISOR.require_pair_proof(self.pair_record(), "a" * 40)
        with self.assertRaisesRegex(
            SUPERVISOR.BootBlocked, "PAIRED_OCI_REVISION_MISMATCH"
        ):
            SUPERVISOR.require_pair_proof(self.pair_record(), "d" * 40)

    def test_paired_receipt_rejects_missing_source_commit(self) -> None:
        """A valid image digest cannot replace signed source provenance."""
        for missing in ("source_commit", "source_sha256"):
            with self.subTest(missing=missing):
                record = self.pair_record()
                record.pop(missing)
                with self.assertRaises(SUPERVISOR.BootBlocked):
                    SUPERVISOR.require_pair_proof(record, "a" * 40)

    def test_paired_receipt_requires_a_guarded_transaction_token(self) -> None:
        """A source pair without durable transaction identity fails boot."""
        for value in (None, "", "qa-slashed/token", "x"):
            with self.subTest(value=value):
                record = self.pair_record()
                if value is None:
                    record.pop("transaction_token")
                else:
                    record["transaction_token"] = value
                with self.assertRaisesRegex(
                    SUPERVISOR.BootBlocked, "PAIRED_TRANSACTION_MISSING"
                ):
                    SUPERVISOR.require_pair_proof(record, "a" * 40)

    def test_paired_receipt_rejects_forged_boolean_flags(self) -> None:
        """No truthy integer or absent proof may bypass release checks."""
        for key in (
            "source_signature_verified",
            "oci_signature_verified",
            "oci_attestations_verified",
        ):
            with self.subTest(key=key):
                record = self.pair_record()
                record[key] = 1
                with self.assertRaisesRegex(
                    SUPERVISOR.BootBlocked, "PAIRED_PROOF_INCOMPLETE"
                ):
                    SUPERVISOR.require_pair_proof(record, "a" * 40)

    def test_paired_receipt_requires_exact_proof_status(self) -> None:
        """A SHA-only receipt is not the signed source+OCI pair proof."""
        record = self.pair_record()
        record["pair_status"] = "PASS_QA_IDENTITY_AND_WAL_ONLY"
        with self.assertRaisesRegex(
            SUPERVISOR.BootBlocked, "PAIRED_PROOF_MISSING"
        ):
            SUPERVISOR.require_pair_proof(record, "a" * 40)

    def mock_root_receipt(self, record: dict):
        """Simulate root-owned mode0600 metadata without privileged files."""
        content = json.dumps(record)
        source = Mock()
        source.lstat.return_value = SimpleNamespace(
            st_mode=stat.S_IFREG | 0o600,
            st_uid=0,
            st_nlink=1,
            st_size=len(content),
        )
        source.read_text.return_value = content
        return source

    def paired_storage_fixture(self) -> None:
        """Make synthetic inspect facts match the selected host paths."""
        self.info["Mounts"][0]["Type"] = "bind"
        self.info["Mounts"].append(
            {
                "Type": "bind",
                "Source": str(self.db.parent),
                "Destination": "/data",
                "RW": True,
            }
        )
        self.info["Config"]["Env"] = [
            f"DATABASE_PATH=/data/{self.db.name}",
            "SVT_WRITER_FENCE_DIR=/run/svt-fence",
        ]

    def test_paired_storage_exact_bind(self) -> None:
        """Strict storage checks require matching path, mounts and env."""
        self.paired_storage_fixture()
        SUPERVISOR.require_paired_storage(self.info, self.args)

    def test_paired_storage_wrong_data_or_nested_mount(self) -> None:
        """Reject different history and shadowed database/fence mounts."""
        self.paired_storage_fixture()
        data = self.info["Mounts"][1]
        for key, value in (
            ("Source", str(self.root / "other")),
            ("Type", "volume"),
            ("RW", False),
        ):
            with (
                self.subTest(key=key),
                patch.dict(data, {key: value}),
                self.assertRaisesRegex(
                    SUPERVISOR.BootBlocked, "STORAGE_MOUNT_MISMATCH"
                ),
            ):
                SUPERVISOR.require_paired_storage(self.info, self.args)
        for target in ("/data/history", "/run", "/run/svt-fence/lock"):
            self.info["Mounts"].append(dict(data, Destination=target))
            with self.assertRaisesRegex(
                SUPERVISOR.BootBlocked, "STORAGE_MOUNT_OVERLAP"
            ):
                SUPERVISOR.require_paired_storage(self.info, self.args)
            self.info["Mounts"].pop()

    def test_paired_storage_rejects_missing_wrong_or_duplicate_env(
        self,
    ) -> None:
        """The app must use the verified DB and fence, not another path."""
        self.paired_storage_fixture()
        env = self.info["Config"]["Env"]
        for values in (
            env[:1],
            ["DATABASE_PATH=/data/other.sqlite3", env[1]],
            env + ["DATABASE_PATH=/data/other.sqlite3"],
        ):
            with (
                self.subTest(values=values),
                patch.dict(self.info["Config"], {"Env": values}),
                self.assertRaisesRegex(
                    SUPERVISOR.BootBlocked, "STORAGE_ENV_MISMATCH"
                ),
            ):
                SUPERVISOR.require_paired_storage(self.info, self.args)

    def test_strict_and_offline_modes_conflict(self) -> None:
        """Strict receipt mode cannot silently take an offline branch."""
        self.args.require_pair_receipt = True
        with self.assertRaisesRegex(
            SUPERVISOR.BootBlocked, "STRICT_QA_MODE_CONFLICT"
        ):
            SUPERVISOR.validate_container(self.info, self.args)

    def test_actual_candidate_checks_complete_paired_receipt(self) -> None:
        """Require root-only receipt and immutable Docker revision at boot."""
        self.args.qa_offline = False
        self.args.require_pair_receipt = True
        self.args.receipt = self.mock_root_receipt(self.pair_record())
        self.info["Config"]["Image"] = (
            "ghcr.io/isentinel/shelly-voltage-telegram@sha256:" + "b" * 64
        )
        self.info["Config"]["Labels"] = {
            "org.opencontainers.image.revision": "a" * 40
        }
        self.args.source_archive_dir = self.root / "source"
        self.paired_storage_fixture()
        with (
            patch.object(
                SUPERVISOR, "source_version", return_value="0.5.26-hw"
            ),
            patch.object(SUPERVISOR, "require_root_ancestors"),
            patch.object(SUPERVISOR, "verify_paired_source_archive"),
        ):
            SUPERVISOR.validate_container(self.info, self.args)

    def test_candidate_rejects_old_digest_only_receipt(self) -> None:
        """A running service must not infer paired proof from old metadata."""
        self.args.qa_offline = False
        self.args.require_pair_receipt = True
        record = {"version": "0.5.26-hw", "digest": "sha256:" + "b" * 64}
        self.args.receipt = self.mock_root_receipt(record)
        self.info["Config"]["Image"] = (
            "ghcr.io/isentinel/shelly-voltage-telegram@sha256:" + "b" * 64
        )
        self.info["Config"]["Labels"] = {
            "org.opencontainers.image.revision": "a" * 40
        }
        with (
            patch.object(
                SUPERVISOR, "source_version", return_value="0.5.26-hw"
            ),
            patch.object(SUPERVISOR, "require_root_ancestors"),
            self.assertRaisesRegex(
                SUPERVISOR.BootBlocked, "SVT_PAIRED_COMMIT_INVALID"
            ),
        ):
            SUPERVISOR.validate_container(self.info, self.args)

    def test_paired_candidate_requires_persisted_source_archive(self) -> None:
        """A digest and source receipt cannot replace the original tarball."""
        self.args.qa_offline = False
        self.args.require_pair_receipt = True
        self.args.receipt = self.mock_root_receipt(self.pair_record())
        self.info["Config"]["Image"] = SUPERVISOR.IMAGE + "sha256:" + "b" * 64
        self.info["Config"]["Labels"] = {
            "org.opencontainers.image.revision": "a" * 40
        }
        with (
            patch.object(
                SUPERVISOR, "source_version", return_value="0.5.26-hw"
            ),
            patch.object(SUPERVISOR, "require_root_ancestors"),
            self.assertRaisesRegex(
                SUPERVISOR.BootBlocked, "PAIRED_SOURCE_DIR_MISSING"
            ),
        ):
            SUPERVISOR.validate_container(self.info, self.args)

    def test_missing_image_revision_fails_closed(self) -> None:
        """No OCI source revision label means no source/OCI identity."""
        for value in (None, {}, {"Labels": None}):
            with self.subTest(value=value):
                self.assertIsNone(SUPERVISOR.image_revision({"Config": value}))

    def test_world_writable_receipt_parent_is_rejected(self) -> None:
        """A receipt under /tmp cannot be a trusted deployment proof."""
        path = self.root / "receipt.json"
        with self.assertRaisesRegex(
            SUPERVISOR.BootBlocked, "SIGNED_RECEIPT_PARENT_UNSAFE"
        ):
            SUPERVISOR.require_root_ancestors(path)

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

    def test_start_and_pending_check_share_one_transaction_gate(self):
        """Updater cannot publish pending between check and Docker start."""
        attempting = threading.Event()
        acquired = threading.Event()
        thread = None

        def updater():
            attempting.set()
            with SUPERVISOR.update_gate(self.db):
                acquired.set()

        def docker_start(*args):
            nonlocal thread
            self.assertEqual(args, ("start", self.identity))
            thread = threading.Thread(target=updater, daemon=True)
            thread.start()
            self.assertTrue(attempting.wait(1))
            self.assertFalse(acquired.wait(0.08))
            return SimpleNamespace(returncode=0)

        with (
            patch.dict(os.environ, {"SVT_WRITER_FENCE_DIR": ""}),
            patch.object(SUPERVISOR, "inspect", return_value=self.info),
            patch.object(SUPERVISOR, "docker", side_effect=docker_start),
        ):
            self.assertTrue(
                SUPERVISOR.start_if_clear(self.args, self.identity)
            )
        self.assertIsNotNone(thread)
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertTrue(acquired.is_set())

    def test_pending_prevents_docker_start_while_gate_is_owned(self):
        """Refuse any Docker call when pending is already published."""
        with (
            patch.dict(os.environ, {"SVT_WRITER_FENCE_DIR": ""}),
            patch.object(
                SUPERVISOR,
                "_pending_under_gate",
                return_value=True,
            ),
            patch.object(SUPERVISOR, "docker") as docker_call,
        ):
            started = SUPERVISOR.start_if_clear(self.args, self.identity)
        self.assertFalse(started)
        docker_call.assert_not_called()

    def test_failed_docker_start_does_not_claim_supervisor_success(self):
        """A nonzero Docker exit must never be a successful writer start."""
        with (
            patch.dict(os.environ, {"SVT_WRITER_FENCE_DIR": ""}),
            patch.object(
                SUPERVISOR, "_pending_under_gate", return_value=False
            ),
            patch.object(SUPERVISOR, "inspect", return_value=self.info),
            patch.object(
                SUPERVISOR,
                "docker",
                return_value=SimpleNamespace(returncode=1),
            ),
            self.assertRaisesRegex(
                SUPERVISOR.BootBlocked, "SVT_DOCKER_START_FAILED"
            ),
        ):
            SUPERVISOR.start_if_clear(self.args, self.identity)

    def test_replaced_container_cannot_be_started(self):
        """A recreated name is not the container approved at entry."""
        replacement = dict(self.info, Id="b" * 64)
        with (
            patch.dict(os.environ, {"SVT_WRITER_FENCE_DIR": ""}),
            patch.object(SUPERVISOR, "inspect", return_value=replacement),
            patch.object(SUPERVISOR, "docker") as docker_call,
            self.assertRaisesRegex(
                SUPERVISOR.BootBlocked, "SVT_CONTAINER_ID_CHANGED"
            ),
        ):
            SUPERVISOR.start_if_clear(self.args, self.identity)
        docker_call.assert_not_called()

    def test_restart_policy_drift_before_start_is_blocked(self):
        """Revalidate the restart policy on subsequent starts as well."""
        self.info["HostConfig"]["RestartPolicy"]["Name"] = "always"
        with (
            patch.dict(os.environ, {"SVT_WRITER_FENCE_DIR": ""}),
            patch.object(SUPERVISOR, "inspect", return_value=self.info),
            patch.object(SUPERVISOR, "docker") as docker_call,
            self.assertRaisesRegex(
                SUPERVISOR.BootBlocked, "RESTART_OWNER_UNFENCED"
            ),
        ):
            SUPERVISOR.start_if_clear(self.args, self.identity)
        docker_call.assert_not_called()

    def test_invalid_immutable_container_id(self):
        """A name, a shortened ID, or absent ID cannot be pinned."""
        for value in ("name", "a" * 12, None):
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(
                    SUPERVISOR.BootBlocked, "SVT_CONTAINER_ID_INVALID"
                ),
            ):
                SUPERVISOR.container_identity({"Id": value})

    def test_symlink_pending_rejected_before_docker_start(self):
        """Even a dangling marker blocks a new owner before Docker."""
        marker = self.root / ".svt-update-pending"
        marker.symlink_to(self.root / "missing-pending")
        with (
            patch.dict(os.environ, {"SVT_WRITER_FENCE_DIR": ""}),
            patch.object(SUPERVISOR, "docker") as docker_call,
            self.assertRaisesRegex(
                SUPERVISOR.BootBlocked, "SVT_PENDING_MARKER_UNSAFE"
            ),
        ):
            SUPERVISOR.start_if_clear(self.args, self.identity)
        docker_call.assert_not_called()

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
