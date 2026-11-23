"""Exercise the signed cutover and its interruption points with real SQLite."""

import importlib.util
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing, nullcontext
from pathlib import Path
from unittest.mock import patch

from scripts import github_update

SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts/signed-forward-cutover.py"
)
SPEC = importlib.util.spec_from_file_location("svt_forward", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
CUTOVER = importlib.util.module_from_spec(SPEC)
with patch.dict(
    sys.modules, {"github_update": github_update, "svt_forward": CUTOVER}
):
    SPEC.loader.exec_module(CUTOVER)

OLD = {"version": "0.5.25-hw", "digest": "sha256:" + "a" * 64}
NEW = {
    "version": "0.5.27-hw",
    "digest": "sha256:" + "b" * 64,
    "commit": "c" * 40,
    "archive_sha256": "d" * 64,
}
PHOTO = "1261"
CURRENT_UNIT = (
    "[Service]\nExecStart=/bin/bash /opt/shelly-voltage/scripts/"
    "apply-update.sh --guarded-start\nExecReload=/bin/bash "
    "/opt/shelly-voltage/scripts/apply-update.sh --guarded-start\n"
)


def fixture_source(directory: Path, version: str) -> None:
    """Create the version markers and service files used by a fixture."""
    directory.mkdir()
    for name in ("VERSION", "RELEASE_VERSION"):
        (directory / name).write_text(version + "\n")
    (directory / "scripts").mkdir()
    (directory / "systemd").mkdir()
    (directory / "systemd" / CUTOVER.MONITOR).write_text(CURRENT_UNIT)
    (directory / "compose.yaml").write_text("services: {}\n")


class ForwardCutoverTests(unittest.TestCase):
    """Keep the SQLite file in place through success and recovery."""

    def setUp(self) -> None:
        """Provide only fixture paths and fake service lifecycle commands."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        self.paths = CUTOVER.Layout(
            source=base / "installed",
            state=base / "state",
            database=base / "history.sqlite3",
            unit=base / "monitor.service",
            operator_lock=base / "operator.lock",
            updater_lock=base / "update.lock",
            overlay=base / "yasno.yaml",
            public_state=base / "public",
        )
        self.work = base / "transaction"
        self.work.mkdir()
        self.runtime = (
            CUTOVER.image_pin(OLD["digest"]),
            "running",
            "unless-stopped",
        )
        self.timer = "active"
        self.calls = []
        self.failed_reload = False
        self.prepare_disk()
        self.addCleanup(patch.stopall)
        patch.object(
            CUTOVER, "safe_path", side_effect=lambda p, **kw: p.lstat()
        ).start()
        patch.object(CUTOVER, "run", side_effect=self.command).start()
        patch.object(CUTOVER, "lease", return_value=nullcontext()).start()
        patch.object(CUTOVER.updater, "preflight").start()
        patch.object(CUTOVER.time, "sleep").start()

    def prepare_disk(self) -> None:
        """Create a source tree, receipts and committed history."""
        paths = self.paths
        fixture_source(paths.source, OLD["version"])
        fixture_source(self.work / "next", NEW["version"])
        (paths.source / ".env").write_text("PRESERVE=1\n")
        (paths.source / "secrets").mkdir()
        (paths.source / "secrets" / "fixture").write_text("synthetic")
        paths.state.mkdir()
        CUTOVER.record(paths.state / "deployed.json", OLD)
        pin = CUTOVER.image_pin(OLD["digest"])
        (paths.state / "override.yaml").write_text(
            f"services:\n  shelly-voltage:\n    image: {pin}\n"
        )
        paths.unit.write_text(OLD["version"])
        with closing(sqlite3.connect(paths.database)) as db, db:
            db.executescript(
                "PRAGMA journal_mode=WAL;"
                "CREATE TABLE measurements(value INTEGER);"
                "INSERT INTO measurements VALUES(222);"
                "CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);"
            )
            db.executemany(
                "INSERT INTO meta VALUES (?,?)",
                (
                    ("telegram_status_message_id", PHOTO),
                    ("telegram_status_media_identity", f"photo:-101:{PHOTO}"),
                ),
            )
        self.inode = paths.database.stat().st_ino

    def command(self, *args: str, **_kwargs) -> str:
        """Dispatch fake service commands and use real file operations."""
        self.calls.append(args)
        if args[0] == "cp":
            child, target = Path(args[-2]), Path(args[-1])
            (shutil.copytree if child.is_dir() else shutil.copy2)(
                child, target
            )
            return ""
        if args[:2] == ("docker", "inspect"):
            if args[3] == "{{json .Mounts}}":
                return json.dumps(
                    [
                        {
                            "Destination": "/data",
                            "Source": str(self.paths.database.parent),
                            "Type": "bind",
                            "RW": True,
                        }
                    ]
                )
            if "DATABASE_PATH" in args[3]:
                return f"DATABASE_PATH=/data/{self.paths.database.name}"
            return "|".join(self.runtime)
        if args[:2] == ("docker", "update"):
            self.runtime = (*self.runtime[:2], args[2].split("=")[-1])
            return ""
        if args[:2] == ("docker", "compose"):
            return self.compose_config()
        if args[0] == "sync":
            return ""
        if args[0] == "systemctl":
            return self.systemd(args)
        raise AssertionError(args)

    def compose_config(self) -> str:
        """Return the fixture's effective candidate mount and image."""
        monitor = {
            "image": CUTOVER.image_pin(NEW["digest"]),
            "restart": "no",
            "environment": {
                "DATABASE_PATH": f"/data/{self.paths.database.name}"
            },
            "volumes": [
                {
                    "type": "bind",
                    "target": "/data",
                    "source": str(self.paths.database.parent),
                }
            ],
        }
        return json.dumps({"services": {"shelly-voltage": monitor}})

    def systemd(self, args: tuple[str, ...]) -> str:
        """Reject an unsafe startup even when the test injected an error."""
        if args[1] == "show":
            if args[3] == "FragmentPath":
                return str(self.paths.unit)
            if args[3] == "DropInPaths":
                return ""
            if args[3] == "UnitFileState":
                return "enabled"
            if args[-1] == CUTOVER.MONITOR:
                return "active" if self.runtime[1] == "running" else "inactive"
            return (
                self.timer if args[-1] == CUTOVER.PHOTO_TIMER else "inactive"
            )
        if args[1] == "daemon-reload" and self.failed_reload:
            self.failed_reload = False
            raise ValueError("injected reload failure")
        if args[1] == "is-enabled":
            return "enabled"
        if args[-1] == CUTOVER.MONITOR and args[1] == "stop":
            self.runtime = (self.runtime[0], "exited", self.runtime[2])
        if args[-1] == CUTOVER.MONITOR and args[1] == "start":
            if self.paths.marker.exists():
                raise AssertionError("startup raced the pending journal")
            selected = CUTOVER.read_pair(self.paths)
            self.runtime = (
                CUTOVER.image_pin(selected["digest"]),
                "running",
                "no",
            )
        if args[-1] == CUTOVER.PHOTO_TIMER and args[1] == "stop":
            self.timer = "inactive"
        if args[-1] == CUTOVER.PHOTO_TIMER and args[1] == "start":
            self.timer = "active"
        return ""

    def journal(self) -> dict:
        """Read the durable local transaction status."""
        return json.loads((self.work / "transaction.json").read_text())

    def assert_history(self, values: list[int]) -> None:
        """Check both SQLite identity and all expected observations."""
        self.assertEqual(self.paths.database.stat().st_ino, self.inode)
        with closing(sqlite3.connect(self.paths.database)) as db:
            rows = db.execute("SELECT value FROM measurements").fetchall()
            self.assertEqual([value for (value,) in rows], values)
            self.assertIn(PHOTO, CUTOVER.photo_identity(db, PHOTO))

    def test_signed_pair_commits_without_restoring_database(self) -> None:
        """The new source, immutable digest, unit and existing photo agree."""
        CUTOVER.activate(self.paths, self.work, NEW, PHOTO)
        self.assertEqual(self.journal()["phase"], "verified")
        self.assertEqual(CUTOVER.read_pair(self.paths), NEW)
        self.assertEqual(self.paths.unit.read_text(), CURRENT_UNIT)
        self.assertFalse(self.paths.marker.exists())
        self.assertEqual(self.timer, "inactive")
        self.assertEqual(
            (self.paths.source / ".env").read_text(), "PRESERVE=1\n"
        )
        self.assertTrue((self.work / "database-before.sqlite3").exists())
        receipt = json.loads(
            (self.paths.public_state / "deployed.json").read_text()
        )
        self.assertEqual(
            receipt, {key: NEW[key] for key in ("version", "digest")}
        )
        self.assert_history([222])

    def test_preboot_failure_restores_the_original_pair(self) -> None:
        """A failed switch repairs source, receipt, unit and restart mode."""
        self.failed_reload = True
        with self.assertRaisesRegex(ValueError, "injected reload failure"):
            CUTOVER.activate(self.paths, self.work, NEW, PHOTO)
        self.assertEqual(CUTOVER.read_pair(self.paths), OLD)
        self.assertEqual(self.paths.unit.read_text(), OLD["version"])
        self.assertFalse(self.paths.marker.exists())
        self.assertEqual(self.journal()["phase"], "recovered")
        self.assertEqual(
            self.runtime,
            (CUTOVER.image_pin(OLD["digest"]), "running", "unless-stopped"),
        )
        self.assertEqual(self.timer, "active")
        self.assert_history([222])

    def test_recovers_interruption_between_source_renames(self) -> None:
        """The durable marker permits recovery with the live path absent."""
        saved = CUTOVER.capture(self.paths, self.work, NEW, PHOTO)
        CUTOVER.save_phase(self.work, saved, "preboot")
        CUTOVER.freeze(self.paths, self.work, saved)
        os.rename(self.paths.source, self.work / "previous")
        CUTOVER.recover(self.paths, self.work)
        self.assertEqual(CUTOVER.read_pair(self.paths), OLD)
        self.assertEqual(self.journal()["phase"], "recovered")
        self.assert_history([222])

    def test_postboot_failure_never_rewinds_new_observations(self) -> None:
        """The old writer stays stopped after the new pair has started."""

        def fail(*_args):
            with closing(sqlite3.connect(self.paths.database)) as db, db:
                db.execute("INSERT INTO measurements VALUES (223)")
            raise ValueError("candidate not ready")

        with (
            patch.object(CUTOVER, "verify_ready", side_effect=fail),
            self.assertRaisesRegex(ValueError, "candidate not ready"),
        ):
            CUTOVER.activate(self.paths, self.work, NEW, PHOTO)
        self.assertEqual(CUTOVER.read_pair(self.paths), NEW)
        self.assertEqual(self.journal()["phase"], "candidate-may-have-run")
        CUTOVER.recover(self.paths, self.work)
        self.assertEqual(self.journal()["phase"], "verified")
        self.assert_history([222, 223])

    def test_foreign_pending_marker_blocks_any_change(self) -> None:
        """Never consume or overwrite another updater's recovery journal."""
        self.paths.marker.write_text("another transaction")
        with self.assertRaisesRegex(ValueError, "already pending"):
            CUTOVER.activate(self.paths, self.work, NEW, PHOTO)
        self.assertEqual(self.paths.marker.read_text(), "another transaction")
        self.assertEqual(CUTOVER.read_pair(self.paths), OLD)
        self.assertEqual(self.calls, [])
        self.assert_history([222])

    def test_archive_replacement_after_verification_is_rejected(self) -> None:
        """Check the exact approved bytes again before opening the archive."""
        archive = self.work / "changed.tar.gz"
        archive.write_bytes(b"changed after verification")
        with self.assertRaisesRegex(ValueError, "archive changed"):
            CUTOVER.stage_source(archive, self.work, NEW)

    def test_rejects_docker_mount_that_can_replace_release_code(self) -> None:
        """Do not accept an image that will execute bind-mounted code."""
        mounts = [
            ("/data", str(self.paths.database.parent), "bind", True),
            ("/app/app.py", "/host/replacement.py", "bind", False),
        ]
        with self.assertRaisesRegex(ValueError, "override release files"):
            CUTOVER.verify_database_mount(mounts, self.paths)

    def test_candidate_requires_guarded_boot_unit(self) -> None:
        """Reject a source package that enables an unguarded boot path."""
        unit = self.work / "next" / "systemd" / CUTOVER.MONITOR
        unit.write_text("[Service]\nExecStart=/usr/bin/docker compose up -d\n")
        with self.assertRaisesRegex(ValueError, "startup guard"):
            CUTOVER.activate(self.paths, self.work, NEW, PHOTO)
        self.assertEqual(CUTOVER.read_pair(self.paths), OLD)
        self.assertFalse(self.paths.marker.exists())
        self.assert_history([222])

    def test_staged_only_record_cannot_trigger_recovery(self) -> None:
        """Recover accepts interrupted activation, not mere preparation."""
        CUTOVER.capture(self.paths, self.work, NEW, PHOTO)
        with self.assertRaisesRegex(ValueError, "no interrupted cutover"):
            CUTOVER.recover(self.paths, self.work)
        self.assertEqual(CUTOVER.read_pair(self.paths), OLD)
        self.assertFalse(self.paths.marker.exists())

    def test_public_receipt_failure_retries_only_the_new_pair(self) -> None:
        """Keep running the new pair if public status cannot be written."""
        with (
            patch.object(CUTOVER, "publish_version", side_effect=OSError),
            self.assertRaises(OSError),
        ):
            CUTOVER.activate(self.paths, self.work, NEW, PHOTO)
        self.assertEqual(CUTOVER.read_pair(self.paths), NEW)
        self.assertEqual(self.journal()["phase"], "candidate-may-have-run")
        CUTOVER.recover(self.paths, self.work)
        self.assertEqual(self.journal()["phase"], "verified")
        self.assert_history([222])
