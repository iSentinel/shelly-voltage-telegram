"""Fail-closed QA-only update staging and inspection policy checks."""

from __future__ import annotations

import argparse
import importlib.util
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing, nullcontext
from pathlib import Path
from unittest.mock import Mock, patch

SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts/stage-guarded-update.py"
)
SPEC = importlib.util.spec_from_file_location("svt_qa_stage", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
STAGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STAGE)


class GuardedUpdatePolicyTests(unittest.TestCase):
    """Prove only a private, stopped, offline QA writer is eligible."""

    def setUp(self) -> None:
        """Use synthetic paths and restore global env after every QA test."""
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        self.args = argparse.Namespace(
            qa_only=True,
            container="svt-qa-check",
            database=Path("/var/tmp/svt-qa-check/history.sqlite3"),
            fence_dir=Path("/var/lib/svt-qa-check-fence"),
            output=Path("/var/lib/svt-qa-check-rescue/newest.sqlite3"),
            token="qa-check-1",
            dry_run=False,
            inspect=False,
            recover_receipt=False,
            resume_pending=False,
            qa_crash_at=None,
        )
        self.info = self.make_info()

    def make_info(self) -> dict:
        """Build Docker inspect data with exactly three isolated mounts."""
        return {
            "HostConfig": {
                "RestartPolicy": {"Name": "no"},
                "NetworkMode": "none",
                "ReadonlyRootfs": True,
            },
            "Config": {
                "User": "10001:10001",
                "Env": ["SVT_WRITER_FENCE_DIR=/run/svt-fence"],
            },
            "Mounts": [
                {
                    "Source": str(self.args.fence_dir),
                    "Destination": "/run/svt-fence",
                    "RW": True,
                },
                {
                    "Source": str(self.args.database.parent),
                    "Destination": "/data",
                    "RW": True,
                },
                {
                    "Source": str(STAGE.SCRIPT_DIR.parent),
                    "Destination": "/src",
                    "RW": False,
                },
            ],
        }

    def test_qa_only_validation(self) -> None:
        """Reject caller who omits the offline isolation agreement."""
        with patch.object(STAGE.os, "geteuid", return_value=0):
            STAGE.check_paths(self.args)
            self.args.qa_only = False
            with self.assertRaisesRegex(STAGE.StageBlocked, "QA_ONLY"):
                STAGE.check_paths(self.args)

    def test_live_database_path_refused(self) -> None:
        """Production SQLite must never be a staging target."""
        self.args.database = Path("/var/lib/shelly-voltage/voltage.sqlite3")
        with (
            patch.object(STAGE.os, "geteuid", return_value=0),
            self.assertRaisesRegex(STAGE.StageBlocked, "DATABASE_PATH_UNSAFE"),
        ):
            STAGE.check_paths(self.args)

    def test_bad_container_identity_refused(self) -> None:
        """A named production bot cannot be disguised as an offline fixture."""
        self.args.container = "shelly-voltage-bot"
        with (
            patch.object(STAGE.os, "geteuid", return_value=0),
            self.assertRaisesRegex(STAGE.StageBlocked, "CONTAINER_REQUIRED"),
        ):
            STAGE.check_paths(self.args)

    def test_independent_restart_refused(self) -> None:
        """Docker must not bypass the systemd owner."""
        self.info["HostConfig"]["RestartPolicy"]["Name"] = "unless-stopped"
        with self.assertRaisesRegex(
            STAGE.StageBlocked, "DOCKER_RESTART_UNFENCED"
        ):
            STAGE.check_container(self.info, self.args)

    def test_other_network_and_mount_refused(self) -> None:
        """No network or extraneous host volumes may reach the QA writer."""
        self.info["HostConfig"]["NetworkMode"] = "bridge"
        with self.assertRaisesRegex(STAGE.StageBlocked, "NETWORK_ENABLED"):
            STAGE.check_container(self.info, self.args)
        self.info["HostConfig"]["NetworkMode"] = "none"
        self.info["Mounts"].append(
            {"Source": "/etc", "Destination": "/host-etc", "RW": False}
        )
        with self.assertRaisesRegex(STAGE.StageBlocked, "MOUNTS_MISMATCH"):
            STAGE.check_container(self.info, self.args)

    def test_rootfs_and_uid_violations_refused(self) -> None:
        """Require a read-only container running without root privileges."""
        self.info["HostConfig"]["ReadonlyRootfs"] = False
        with self.assertRaisesRegex(STAGE.StageBlocked, "ROOTFS_WRITABLE"):
            STAGE.check_container(self.info, self.args)
        self.info["HostConfig"]["ReadonlyRootfs"] = True
        self.info["Config"]["User"] = "0:0"
        with self.assertRaisesRegex(STAGE.StageBlocked, "USER_MISMATCH"):
            STAGE.check_container(self.info, self.args)

    def test_sqlite_digest_detects_changed_photo(self) -> None:
        """Equivalent counts do not hide a modified Telegram identity."""
        with tempfile.TemporaryDirectory(prefix="svt-qa-policy-") as temp:
            db_path = Path(temp) / "isolated.sqlite3"
            self.create_fixture(db_path)
            original = STAGE.database_digest(db_path)
            with closing(sqlite3.connect(db_path)) as db, db:
                db.execute(
                    "UPDATE meta SET value=? WHERE key=?",
                    ("photo:FORGED", "telegram_status_media_identity"),
                )
            self.assertNotEqual(original, STAGE.database_digest(db_path))

    def create_fixture(self, path: Path) -> None:
        """Construct one standalone three-phase history with saved PHOTO."""
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript(
                "CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);"
                "CREATE TABLE measurements("
                "ts REAL,voltage REAL,frequency REAL,phase TEXT);"
                "INSERT INTO meta VALUES("
                "'telegram_setting_meter_type','pro3em');"
                "INSERT INTO meta VALUES("
                "'telegram_status_media_identity','photo:QA');"
                "INSERT INTO measurements VALUES(1000,220,50,'L1');"
                "INSERT INTO measurements VALUES(1000,221,50,'L2');"
                "INSERT INTO measurements VALUES(1000,222,50,'L3');"
            )

    def test_fault_injection_only_kills_current_qa_process(self) -> None:
        """The deliberate crash is limited to the current QA updater."""
        self.args.qa_crash_at = "pending"
        with (
            patch.object(STAGE.os, "kill") as kill,
            patch.object(STAGE.os, "getpid", return_value=12345),
        ):
            STAGE.qa_checkpoint(self.args, "writer-stopped")
            kill.assert_not_called()
            STAGE.qa_checkpoint(self.args, "pending")
        kill.assert_called_once_with(12345, STAGE.signal.SIGKILL)

    def test_resume_must_verify_pending_before_docker_stop(self) -> None:
        """Wrong pending token must block before stopping a QA writer."""
        with (
            patch.object(
                STAGE,
                "fence_directory",
                return_value=self.args.fence_dir,
            ),
            patch.object(
                STAGE,
                "read_pending_token",
                side_effect=STAGE.StageBlocked(
                    "SVT_QA_PENDING_TOKEN_MISMATCH"
                ),
            ),
            patch.object(STAGE, "stop_writer") as stop,
            self.assertRaisesRegex(
                STAGE.StageBlocked, "SVT_QA_PENDING_TOKEN_MISMATCH"
            ),
        ):
            STAGE.resume_pending(self.args, {"State": {"Running": True}})
        stop.assert_not_called()

    def test_resume_after_sigkill_stops_writer_before_rescue(self) -> None:
        """Stop a pending QA writer, rescue newest WAL, retain the marker."""
        with tempfile.TemporaryDirectory() as temp:
            self.args.output = Path(temp) / "newest.sqlite3"
            calls = []

            def called(name):
                return lambda *_: calls.append(name)

            with (
                patch.object(STAGE, "require_private_output_dir"),
                patch.object(
                    STAGE, "fence_directory", return_value=self.args.fence_dir
                ),
                patch.object(
                    STAGE, "read_pending_token", side_effect=called("marker")
                ),
                patch.object(STAGE, "stop_writer", side_effect=called("stop")),
                patch.object(
                    STAGE, "rescue_newest", side_effect=called("rescue")
                ),
                patch.object(
                    STAGE, "persist_receipt", side_effect=called("receipt")
                ),
                patch.object(
                    STAGE,
                    "inspect_container",
                    return_value={"State": {"Running": False}},
                ),
                patch.object(
                    STAGE, "inspect_stage", side_effect=called("inspect")
                ),
            ):
                STAGE.resume_pending(self.args, {"State": {"Running": True}})
            self.assertEqual(
                calls, ["marker", "stop", "rescue", "receipt", "inspect"]
            )

    def test_stale_rescue_cannot_be_overwritten_on_resume(self) -> None:
        """A rescue collision blocks before modifying existing backup data."""
        with tempfile.TemporaryDirectory() as temp:
            self.args.output = Path(temp) / "newest.sqlite3"
            self.args.output.write_bytes(b"tampered")
            with (
                patch.object(STAGE, "require_private_output_dir"),
                patch.object(
                    STAGE,
                    "fence_directory",
                    return_value=self.args.fence_dir,
                ),
                patch.object(STAGE, "read_pending_token"),
                patch.object(
                    STAGE,
                    "check_rescue_file",
                    side_effect=STAGE.StageBlocked("SVT_QA_RESCUE_UNSAFE"),
                ),
                patch.object(STAGE, "rescue_newest") as rescue,
                patch.object(STAGE, "persist_receipt") as receipt,
                self.assertRaisesRegex(
                    STAGE.StageBlocked, "SVT_QA_RESCUE_UNSAFE"
                ),
            ):
                STAGE.resume_pending(self.args, {"State": {"Running": False}})
            rescue.assert_not_called()
            receipt.assert_not_called()
            self.assertEqual(self.args.output.read_bytes(), b"tampered")

    def test_existing_rescue_reverified_under_writer_lease(self) -> None:
        """Existing rescue must match current PHOTO; no duplicate backup."""
        with tempfile.TemporaryDirectory() as temp:
            self.args.output = Path(temp) / "newest.sqlite3"
            self.args.output.write_bytes(b"existing")
            events = []

            def called(name):
                return Mock(side_effect=lambda *_: events.append(name))

            with (
                patch.object(STAGE, "require_private_output_dir"),
                patch.object(
                    STAGE, "fence_directory", return_value=self.args.fence_dir
                ),
                patch.object(
                    STAGE, "quiescent_writer_lease", return_value=nullcontext()
                ),
                patch.object(STAGE, "rescue_newest") as rescue,
                patch.multiple(
                    STAGE,
                    read_pending_token=called("marker"),
                    check_rescue_file=Mock(),
                    validate_staged_history=called("parity"),
                    persist_receipt=called("receipt"),
                    inspect_container=Mock(
                        return_value={"State": {"Running": False}}
                    ),
                    inspect_stage=called("inspect"),
                ),
            ):
                STAGE.resume_pending(self.args, {"State": {"Running": False}})
            rescue.assert_not_called()
            self.assertEqual(
                events, ["marker", "marker", "parity", "receipt", "inspect"]
            )

    def test_error_receipt_never_authorizes_release(self) -> None:
        """The QA stage always fails with no release authorization."""
        with patch.object(STAGE, "arguments", return_value=self.args):
            self.args.container = "shelly-voltage-bot"
            code = STAGE.main()
        self.assertEqual(code, 3)


if __name__ == "__main__":
    unittest.main()
