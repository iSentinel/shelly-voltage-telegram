"""Exercise the signed cutover and its interruption points with real SQLite."""

import argparse
import hashlib
import importlib.util
import io
import json
import os
import shutil
import sqlite3
import sys
import tarfile
import tempfile
import unittest
from contextlib import closing, nullcontext, redirect_stdout
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
LEGACY_UNIT = (
    "[Service]\n"
    f"ExecStartPre={CUTOVER.LEGACY_RECOVER}\n"
    "ExecStart=/bin/bash /opt/shelly-voltage/scripts/"
    "start-compose.sh\nExecReload=/bin/bash "
    "/opt/shelly-voltage/scripts/start-compose.sh\n"
)


def fixture_source(directory: Path, version: str) -> None:
    """Create the version markers and service files used by a fixture."""
    directory.mkdir()
    for name in ("VERSION", "RELEASE_VERSION"):
        (directory / name).write_text(version + "\n")
    (directory / "scripts").mkdir()
    (directory / "scripts" / "apply-update.sh").write_text("#!/bin/bash\n")
    (directory / "app.py").write_text('"""Legacy-compatible fixture."""\n')
    (directory / "systemd").mkdir()
    (directory / "systemd" / CUTOVER.MONITOR).write_text(CURRENT_UNIT)
    (directory / "compose.yaml").write_text("services: {}\n")


def rollback_archive(
    path: Path, version: str, app: bytes = b"monitor source"
) -> str:
    """Build a read-only preflight fixture with exact archive inputs."""
    with tarfile.open(path, "w:gz") as tar:
        for name in CUTOVER.MONITOR_FILES | CUTOVER.ROLLBACK_SUPERVISOR_FILES:
            data = app if name == "app.py" else name.encode()
            member = tarfile.TarInfo(
                f"shelly-voltage-telegram-{version}/{name}"
            )
            member.size = len(data)
            tar.addfile(member, io.BytesIO(data))
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
            fence=base / "fence",
        )
        self.work = base / "transaction"
        self.work.mkdir()
        self.runtime = (
            CUTOVER.image_pin(OLD["digest"]),
            "running",
            "unless-stopped",
        )
        self.timer = "active"
        self.check_timer = "inactive"
        self.check_timer_enabled = False
        self.fail_check_timer = False
        self.supervisor_mode = False
        self.container_exists = True
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
        paths.unit.write_text(LEGACY_UNIT)
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
            return self.inspect_response(args[3])
        if args[:2] == ("docker", "update"):
            self.runtime = (*self.runtime[:2], args[2].split("=")[-1])
            return ""
        if args[:2] == ("docker", "compose"):
            return self.compose_config()
        if args[:2] == ("docker", "ps"):
            return CUTOVER.CONTAINER if self.container_exists else ""
        if args[:2] == ("docker", "rm"):
            self.container_exists = False
            return ""
        if args[0] in ("sync", "systemd-analyze"):
            return ""
        if args[0] == "systemctl":
            return self.systemd(args)
        raise AssertionError(args)

    def inspect_response(self, template: str) -> str:
        """Return only the Docker fields read by the updater."""
        if template == "{{json .Mounts}}":
            mounts = [
                {
                    "Destination": "/data",
                    "Source": str(self.paths.database.parent),
                    "Type": "bind",
                    "RW": True,
                }
            ]
            if self.supervisor_mode:
                mounts.append(
                    {
                        "Destination": CUTOVER.FENCE_TARGET,
                        "Source": str(self.paths.fence),
                        "Type": "bind",
                        "RW": True,
                    }
                )
            return json.dumps(mounts)
        if "DATABASE_PATH" in template:
            return f"DATABASE_PATH=/data/{self.paths.database.name}"
        if "SVT_WRITER_FENCE_DIR" in template:
            return f"SVT_WRITER_FENCE_DIR={CUTOVER.FENCE_TARGET}"
        return "|".join(self.runtime)

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
        if self.supervisor_mode:
            monitor["environment"]["SVT_WRITER_FENCE_DIR"] = (
                CUTOVER.FENCE_TARGET
            )
            monitor["volumes"].append(
                {
                    "type": "bind",
                    "target": CUTOVER.FENCE_TARGET,
                    "source": str(self.paths.fence),
                }
            )
        return json.dumps({"services": {"shelly-voltage": monitor}})

    def systemd_property(self, prop: str, unit: str, owner: str) -> str:
        """Report the installed unit paths and the current service state."""
        if prop == "FragmentPath":
            if unit == CUTOVER.SUPERVISOR:
                return str(self.paths.supervisor_unit)
            if unit in (
                CUTOVER.RELEASE_CHECK_SERVICE,
                CUTOVER.RELEASE_CHECK_TIMER,
            ):
                return str(self.paths.unit.with_name(unit))
            return str(self.paths.unit)
        if prop == "DropInPaths":
            return ""
        if prop == "UnitFileState":
            if unit == CUTOVER.RELEASE_CHECK_TIMER:
                return "enabled" if self.check_timer_enabled else "disabled"
            if self.supervisor_mode and unit != owner:
                return "disabled"
            return "enabled"
        if unit == owner:
            return "active" if self.runtime[1] == "running" else "inactive"
        if unit == CUTOVER.RELEASE_CHECK_TIMER:
            return self.check_timer
        return self.timer if unit == CUTOVER.PHOTO_TIMER else "inactive"

    def systemd(self, args: tuple[str, ...]) -> str:
        """Reject an unsafe startup even when the test injected an error."""
        owner = CUTOVER.SUPERVISOR if self.supervisor_mode else CUTOVER.MONITOR
        if args[1] == "show":
            return self.systemd_property(args[3], args[-1], owner)
        if args[1:] == ("enable", "--now", CUTOVER.RELEASE_CHECK_TIMER):
            if self.fail_check_timer:
                raise ValueError("injected timer failure")
            self.check_timer = "active"
            self.check_timer_enabled = True
            return ""
        if args[-1] == CUTOVER.RELEASE_CHECK_TIMER:
            self.adjust_check_timer(args[1])
            return ""
        if args[1] == "daemon-reload" and self.failed_reload:
            self.failed_reload = False
            raise ValueError("injected reload failure")
        if args[1] == "is-enabled":
            return "enabled"
        if args[-1] == owner and args[1] == "stop":
            self.runtime = (self.runtime[0], "exited", self.runtime[2])
        if args[-1] == owner and args[1] == "start":
            if (
                self.paths.marker.exists()
                or (self.paths.fence / ".svt-update-pending").exists()
            ):
                raise AssertionError("startup raced the pending journal")
            selected = CUTOVER.read_pair(self.paths)
            self.runtime = (
                CUTOVER.image_pin(selected["digest"]),
                "running",
                "no",
            )
            self.container_exists = True
        if args[-1] == CUTOVER.PHOTO_TIMER and args[1] == "stop":
            self.timer = "inactive"
        if args[-1] == CUTOVER.PHOTO_TIMER and args[1] == "start":
            self.timer = "active"
        return ""

    def adjust_check_timer(self, operation: str) -> None:
        """Model the timer activation and enablement separately."""
        if operation in ("enable", "disable"):
            self.check_timer_enabled = operation == "enable"
        elif operation in ("start", "stop"):
            self.check_timer = "active" if operation == "start" else "inactive"

    def existing_supervisor(self) -> dict:
        """Install a previous signed receipt and retained unit fixture."""
        self.supervisor_mode = True
        self.timer = "inactive"
        self.runtime = (*self.runtime[:2], "no")
        paths = self.paths
        paths.fence.mkdir()
        unit_template = (
            f"[Service]\nExecStart=/bin/true {CUTOVER.SOURCE_ARCHIVE_ARG}\n"
        )
        for folder in (paths.source, self.work / "next"):
            (folder / "deploy").mkdir()
            (folder / CUTOVER.SUPERVISOR_TEMPLATE).write_text(unit_template)
            (folder / CUTOVER.SUPERVISOR_OVERLAY).write_text("services: {}")
            (folder / "app.py").write_text('"SVT_FENCE_DIR_REQUIRED"\n')
        token = "cutover-OLD12345"
        prior = paths.home / token
        (prior / "release-archive").mkdir(parents=True)
        archive = (
            prior / "release-archive/shelly-voltage-telegram-0.5.25-hw.tar.gz"
        )
        archive.write_bytes(b"signed archive fixture")
        old = {
            **OLD,
            "transaction_token": token,
            "source_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        }
        CUTOVER.record(paths.state / "deployed.json", old)
        CUTOVER.record(
            prior / "transaction.json", {"phase": "verified", "pair": old}
        )
        paths.unit.write_bytes(CUTOVER.INERT_LEGACY_UNIT)
        paths.supervisor_unit.write_bytes(
            CUTOVER.supervisor_unit(paths.source, prior)
        )
        patch.object(
            CUTOVER, "control_fence", side_effect=self.control_fence
        ).start()
        return old

    def control_fence(self, paths, work, action, _source):
        """Track the same shared pending marker used by the signed writer."""
        marker = paths.fence / ".svt-update-pending"
        if action == "publish":
            self.assertFalse(marker.exists())
            marker.write_text(f"{work.name}\n")
        else:
            self.assertFalse(paths.marker.exists())
            self.assertEqual(marker.read_text(), f"{work.name}\n")
            marker.unlink()

    def journal(self) -> dict:
        """Read the durable local transaction status."""
        return json.loads((self.work / "transaction.json").read_text())

    def rollback_pair(
        self, target: str = "0.5.24-hw", app: bytes = b"monitor source"
    ) -> tuple[argparse.Namespace, dict]:
        """Stage exact source bytes for an installed owner and older target."""
        paths = self.paths
        current = self.existing_supervisor()
        prior = paths.home / current["transaction_token"]
        archive = CUTOVER.retained_archive(prior, current["version"])
        current["source_sha256"] = rollback_archive(
            archive, current["version"]
        )
        CUTOVER.record(paths.state / "deployed.json", current)
        CUTOVER.record(
            prior / "transaction.json", {"phase": "verified", "pair": current}
        )
        archive = self.work / f"shelly-voltage-telegram-{target}.tar.gz"
        pair = {
            **NEW,
            "version": target,
            "archive_sha256": rollback_archive(archive, target, app),
        }
        args = argparse.Namespace(
            action="check-rollback", photo=PHOTO, archive=archive
        )
        return args, pair

    def test_cutover_plan_refuses_unapproved_report_mode(self):
        """Block before stopping the previous writer, not at candidate boot."""
        monitor = json.loads(self.compose_config())["services"][
            "shelly-voltage"
        ]
        for mode in ("paused", "invalid", None, ""):
            monitor["environment"]["SCHEDULED_REPORTS_MODE"] = mode
            with self.assertRaisesRegex(ValueError, "report recovery"):
                CUTOVER.validate_compose(
                    self.paths, monitor, CUTOVER.image_pin(NEW["digest"])
                )

    def test_rollback_preflight_reads_live_state_without_a_lease_or_write(
        self,
    ):
        """Report the verified pair without changing any runtime state."""
        args, pair = self.rollback_pair()
        output = io.StringIO()
        with (
            patch.object(CUTOVER, "options", return_value=args),
            patch.object(CUTOVER, "Layout", return_value=self.paths),
            patch.object(CUTOVER.os, "geteuid", return_value=0),
            patch.object(CUTOVER, "signed_candidate", return_value=pair),
            patch.object(CUTOVER, "lease") as lease,
            redirect_stdout(output),
        ):
            self.assertEqual(CUTOVER.main(), 0)
        lease.assert_not_called()
        report = json.loads(output.getvalue())
        self.assertEqual(report["status"], "PASS_ROLLBACK_PAIR_PREFLIGHT_ONLY")
        self.assertFalse(report["rollback_authorized"])
        self.assertTrue(report["monitor_inputs_match"])
        self.assertTrue(
            all(call[1] in ("show", "inspect") for call in self.calls)
        )
        self.assert_history([222])

    def test_rollback_preflight_rejects_same_version(self):
        """Require an older signed target even when inputs match."""
        args, pair = self.rollback_pair(target=OLD["version"])
        with (
            patch.object(CUTOVER, "signed_candidate", return_value=pair),
            self.assertRaisesRegex(ValueError, "must be older"),
        ):
            CUTOVER.rollback_preflight(self.paths, args)

    def test_rollback_preflight_detects_changed_monitor_source(self):
        """Require a separate compatibility check if inputs differ."""
        args, pair = self.rollback_pair(app=b"changed monitor source")
        with (
            patch.object(CUTOVER, "signed_candidate", return_value=pair),
            self.assertRaisesRegex(ValueError, "build inputs differ"),
        ):
            CUTOVER.rollback_preflight(self.paths, args)
        self.assert_history([222])

    def test_rollback_preflight_rejects_target_archive_changed_after_proof(
        self,
    ):
        """Read only bytes bound to the target signature verification."""
        args, pair = self.rollback_pair()
        args.archive.write_bytes(b"invalid archive")
        with (
            patch.object(CUTOVER, "signed_candidate", return_value=pair),
            self.assertRaisesRegex(ValueError, "source archive changed"),
        ):
            CUTOVER.rollback_preflight(self.paths, args)

    def stage_older_supervisor(self, app: bytes = b"monitor source") -> dict:
        """Retain a verified older pair whose unused legacy unit is old."""
        args, pair = self.rollback_pair(app=app)
        work = self.paths.home / "cutover-NEW12345"
        self.work.rename(work)
        self.work = work
        for name in ("VERSION", "RELEASE_VERSION"):
            self.work.joinpath("next", name).write_text(pair["version"] + "\n")
        self.work.joinpath("next", "systemd", CUTOVER.MONITOR).write_text(
            LEGACY_UNIT
        )
        archive = work / args.archive.name
        return CUTOVER.stage_supervised_receipt(archive, work, pair)

    def activate_older_pair(self, pair: dict) -> None:
        """Run a rollback approved for the fixture's current digest."""
        CUTOVER.activate(
            self.paths,
            self.work,
            pair,
            PHOTO,
            True,
            rollback_from=OLD["digest"],
        )

    def test_supervised_rollback_keeps_sqlite_and_seals_its_rescue(self):
        """Verify the old pair and the checksum of the stopped-writer copy."""
        pair = self.stage_older_supervisor()
        self.activate_older_pair(pair)
        self.assertEqual(self.journal()["phase"], "verified")
        self.assertEqual(CUTOVER.read_pair(self.paths), pair)
        self.assertEqual(
            self.paths.unit.read_bytes(), CUTOVER.INERT_LEGACY_UNIT
        )
        backup = self.work / "database-before.sqlite3"
        checksum = backup.with_suffix(".sqlite3.sha256").read_text().split()[0]
        self.assertEqual(
            checksum, hashlib.sha256(backup.read_bytes()).hexdigest()
        )
        self.assert_history([222])

    def test_supervised_rollback_refuses_different_origin_before_stop(self):
        """Require the approved running digest even if the target matches."""
        pair = self.stage_older_supervisor()
        with self.assertRaisesRegex(ValueError, "approved rollback origin"):
            CUTOVER.activate(
                self.paths,
                self.work,
                pair,
                PHOTO,
                True,
                rollback_from=NEW["digest"],
            )
        self.assertNotIn(("systemctl", "stop", CUTOVER.SUPERVISOR), self.calls)
        self.assertFalse(self.paths.marker.exists())
        self.assert_history([222])

    def test_supervised_rollback_rechecks_compatibility_before_stop(self):
        """Apply the read-only input check again under the activation lock."""
        pair = self.stage_older_supervisor(app=b"different application")
        with self.assertRaisesRegex(ValueError, "build inputs differ"):
            self.activate_older_pair(pair)
        self.assertNotIn(("systemctl", "stop", CUTOVER.SUPERVISOR), self.calls)
        self.assert_history([222])

    def assert_pending_history(self):
        """Read unchanged rows and photo without accepting cutover."""
        self.assertEqual(self.paths.database.stat().st_ino, self.inode)
        with closing(sqlite3.connect(self.paths.database)) as db:
            self.assertEqual(
                db.execute("SELECT value FROM measurements").fetchall(),
                [(222,)],
            )
            self.assertEqual(
                db.execute(
                    "SELECT value FROM meta WHERE key=?",
                    ("telegram_status_media_identity",),
                ).fetchone(),
                (f"photo:-101:{PHOTO}",),
            )
            with self.assertRaisesRegex(ValueError, "report delivery"):
                CUTOVER.photo_identity(db, PHOTO)

    def insert_pending_report(self, key: str) -> None:
        """Persist an unresolved intent without touching photo identity."""
        with closing(sqlite3.connect(self.paths.database)) as db, db:
            db.execute(
                "INSERT INTO meta VALUES (?,?)",
                (f"telegram_report_pending:{key}", "2026-11-26"),
            )

    def test_pending_report_blocks_rollback_before_stop(self):
        """The cutover has its own guard, even with mocked preflight."""
        pair = self.stage_older_supervisor()
        self.insert_pending_report("daily_summary_day")
        with self.assertRaisesRegex(ValueError, "report delivery"):
            self.activate_older_pair(pair)
        self.assertNotIn(("systemctl", "stop", CUTOVER.SUPERVISOR), self.calls)
        self.assertFalse(self.paths.marker.exists())
        self.assert_pending_history()

    def test_pending_report_after_capture_blocks_pair_switch(self):
        """Refuse a late reservation before replacing either signed pair."""
        pair = self.stage_older_supervisor()
        original = CUTOVER.read_pair(self.paths)
        backup = CUTOVER.backup_database

        def late_report(paths, work, photo):
            """Simulate a reservation committed before writer fencing."""
            self.insert_pending_report("daily_summary_day")
            backup(paths, work, photo)

        with (
            patch.object(CUTOVER, "backup_database", side_effect=late_report),
            self.assertRaisesRegex(ValueError, "report delivery"),
        ):
            self.activate_older_pair(pair)
        self.assertEqual(self.journal()["phase"], "previous-pair-restored")
        self.assertEqual(CUTOVER.read_pair(self.paths), original)
        self.assertEqual(
            self.runtime,
            (CUTOVER.image_pin(original["digest"]), "running", "no"),
        )
        self.assertFalse(self.paths.marker.exists())
        with closing(sqlite3.connect(self.paths.database)) as db:
            self.assertEqual(
                db.execute(
                    "SELECT value FROM meta WHERE key=?",
                    ("telegram_report_pending:daily_summary_day",),
                ).fetchone(),
                ("2026-11-26",),
            )
        self.assert_pending_history()

    def test_backup_photo_guard_blocks_weekly_report_intent(self):
        """A weekly unresolved report is as unsafe as a daily one."""
        self.insert_pending_report("telegram_weekly_private_sent_7")
        with self.assertRaisesRegex(ValueError, "report delivery"):
            CUTOVER.backup_database(self.paths, self.work, PHOTO)
        self.assert_pending_history()

    def test_supervised_rollback_aborts_when_the_rescue_fails(self):
        """Keep the original pair when its standalone rescue is invalid."""
        pair = self.stage_older_supervisor()
        original = CUTOVER.read_pair(self.paths)
        with (
            patch.object(
                CUTOVER,
                "seal_rollback_rescue",
                side_effect=ValueError("invalid rescue"),
            ),
            self.assertRaisesRegex(ValueError, "invalid rescue"),
        ):
            self.activate_older_pair(pair)
        self.assertEqual(self.journal()["phase"], "recovered")
        self.assertEqual(CUTOVER.read_pair(self.paths), original)
        self.assertFalse(self.paths.marker.exists())
        self.assert_history([222])

    def test_supervised_rollback_recovers_origin_after_preboot_interrupt(self):
        """Restore the newer pair and its checker after early failure."""
        pair = self.stage_older_supervisor()
        old = CUTOVER.read_pair(self.paths)
        install_pair = CUTOVER.install_pair
        modes = {"active": True, "enabled": True}
        self.check_timer = "active"
        self.check_timer_enabled = True

        def interrupt(*args):
            install_pair(*args)
            raise KeyboardInterrupt

        with (
            patch.object(CUTOVER, "rollback_timer_modes", return_value=modes),
            patch.object(CUTOVER, "install_pair", side_effect=interrupt),
            self.assertRaises(KeyboardInterrupt),
        ):
            self.activate_older_pair(pair)
        self.assertFalse(self.check_timer_enabled)
        self.assertEqual(self.check_timer, "inactive")
        CUTOVER.recover(self.paths, self.work)
        self.assertEqual(CUTOVER.read_pair(self.paths), old)
        self.assertEqual(self.journal()["phase"], "recovered")
        self.assertTrue(self.check_timer_enabled)
        self.assertEqual(self.check_timer, "active")
        self.assert_history([222])

    def test_supervised_rollback_keeps_new_rows_after_old_writer_starts(self):
        """Resume the older writer when it may have written to SQLite."""
        pair = self.stage_older_supervisor()

        def interrupt(*_args):
            with closing(sqlite3.connect(self.paths.database)) as db, db:
                db.execute("INSERT INTO measurements VALUES (223)")
            raise OSError("receipt interrupted")

        with (
            patch.object(CUTOVER, "publish_version", side_effect=interrupt),
            self.assertRaisesRegex(OSError, "receipt interrupted"),
        ):
            self.activate_older_pair(pair)
        self.assertEqual(self.journal()["phase"], "candidate-may-have-run")
        CUTOVER.recover(self.paths, self.work)
        self.assertEqual(self.journal()["phase"], "verified")
        self.assertEqual(CUTOVER.read_pair(self.paths), pair)
        self.assert_history([222, 223])
        self.calls.clear()
        CUTOVER.recover(self.paths, self.work)
        self.assertTrue(
            all(call[1] in ("show", "inspect") for call in self.calls)
        )

    def release_check_pair(self) -> dict:
        """Add signed-input fixtures and inactive stock units to an upgrade."""
        self.existing_supervisor()
        paths = self.paths
        work = paths.home / "cutover-NEW12345"
        self.work.rename(work)
        self.work = work
        prefix = f"shelly-voltage-telegram-{NEW['version']}"
        archive = work / f"{prefix}.tar.gz"
        units = {
            CUTOVER.RELEASE_CHECK_SERVICE: b"LoadCredential=checker.py:test\n",
            CUTOVER.RELEASE_CHECK_TIMER: b"[Timer]\nOnBootSec=10m\n",
        }
        with tarfile.open(archive, "w:gz") as tar:
            for unit, payload in units.items():
                file = work / "next" / "systemd" / unit
                file.write_bytes(payload)
                tar.add(file, f"{prefix}/systemd/{unit}")
                (paths.source / "systemd" / unit).write_bytes(b"old unit\n")
                paths.unit.with_name(unit).write_bytes(b"old unit\n")
            checker = work / "next" / "scripts/github_update.py"
            checker.write_text("# checker fixture\n")
            tar.add(checker, f"{prefix}/scripts/github_update.py")
        pair = {
            **NEW,
            "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        }
        return CUTOVER.stage_supervised_receipt(archive, work, pair)

    def test_supervised_upgrade_starts_the_packaged_release_timer(self):
        """Use the candidate's checker and replace the old stock units."""
        pair = self.release_check_pair()
        CUTOVER.activate(self.paths, self.work, pair, PHOTO, True)
        self.assertEqual(self.journal()["phase"], "verified")
        self.assertEqual(self.check_timer, "active")
        for unit in (
            CUTOVER.RELEASE_CHECK_SERVICE,
            CUTOVER.RELEASE_CHECK_TIMER,
        ):
            expected = self.paths.source / "systemd" / unit
            self.assertEqual(
                self.paths.unit.with_name(unit).read_bytes(),
                expected.read_bytes(),
            )
        self.assert_history([222])

    def test_timer_failure_can_be_retried_without_restarting_the_monitor(self):
        """Leave the verified monitor running when the timer cannot start."""
        pair = self.release_check_pair()
        self.fail_check_timer = True
        CUTOVER.activate(self.paths, self.work, pair, PHOTO, True)
        self.assertEqual(self.journal()["phase"], "verified")
        self.assertEqual(self.check_timer, "inactive")
        self.fail_check_timer = False
        self.calls.clear()
        CUTOVER.sync_release_check(self.paths, self.work)
        self.assertEqual(self.check_timer, "active")
        self.assertNotIn(
            ("systemctl", "start", CUTOVER.SUPERVISOR), self.calls
        )
        self.assert_history([222])

    def test_release_check_preserves_local_unit_edits(self):
        """Check both targets before replacing either installed unit."""
        pair = self.release_check_pair()
        timer = self.paths.unit.with_name(CUTOVER.RELEASE_CHECK_TIMER)
        timer.write_bytes(b"locally modified\n")
        CUTOVER.activate(self.paths, self.work, pair, PHOTO, True)
        self.assertEqual(self.journal()["phase"], "verified")
        with self.assertRaisesRegex(ValueError, "locally modified"):
            CUTOVER.sync_release_check(self.paths, self.work)
        self.assertEqual(timer.read_bytes(), b"locally modified\n")
        service = self.paths.unit.with_name(CUTOVER.RELEASE_CHECK_SERVICE)
        self.assertEqual(service.read_bytes(), b"old unit\n")

    def test_release_check_rejects_a_changed_checker(self):
        """Do not install or start units for an altered host checker."""
        pair = self.release_check_pair()
        checker = self.work / "next" / "scripts/github_update.py"
        checker.write_bytes(b"# changed fixture\n")
        CUTOVER.activate(self.paths, self.work, pair, PHOTO, True)
        with self.assertRaisesRegex(ValueError, "differs from signed source"):
            CUTOVER.sync_release_check(self.paths, self.work)
        self.assertEqual(self.check_timer, "inactive")
        self.assert_history([222])

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

    def test_supervised_upgrade_preserves_fence_and_database(self):
        """Replace a running supervised pair and keep its writer lock."""
        self.existing_supervisor()
        fence = self.paths.fence.stat().st_ino
        CUTOVER.activate(self.paths, self.work, NEW, PHOTO, True)
        self.assertEqual(self.journal()["phase"], "verified")
        self.assertEqual(CUTOVER.read_pair(self.paths), NEW)
        self.assertEqual(self.paths.fence.stat().st_ino, fence)
        self.assertEqual(
            self.paths.unit.read_bytes(), CUTOVER.INERT_LEGACY_UNIT
        )
        self.assertNotIn(("systemctl", "disable", CUTOVER.MONITOR), self.calls)
        self.assertFalse(self.paths.marker.exists())
        self.assertFalse(CUTOVER.fence_pending(self.paths, self.work))
        self.assert_history([222])

    def test_supervised_preboot_interruption_restores_the_previous_unit(self):
        """Recover the previous supervisor after a source and pin switch."""
        old = self.existing_supervisor()
        old_unit = self.paths.supervisor_unit.read_bytes()
        install = CUTOVER.install_pair

        def interrupt(*args):
            install(*args)
            raise KeyboardInterrupt

        with (
            patch.object(CUTOVER, "install_pair", side_effect=interrupt),
            self.assertRaises(KeyboardInterrupt),
        ):
            CUTOVER.activate(self.paths, self.work, NEW, PHOTO, True)
        self.assertTrue(CUTOVER.fence_pending(self.paths, self.work))
        CUTOVER.recover(self.paths, self.work)
        self.assertEqual(self.journal()["phase"], "recovered")
        self.assertEqual(CUTOVER.read_pair(self.paths), old)
        self.assertEqual(self.paths.supervisor_unit.read_bytes(), old_unit)
        self.assertFalse(CUTOVER.fence_pending(self.paths, self.work))
        self.assert_history([222])

    def test_supervised_postboot_recovery_preserves_new_writes(self):
        """After candidate startup, retain it and the newest SQLite history."""
        self.existing_supervisor()

        def interrupt(*_args):
            with closing(sqlite3.connect(self.paths.database)) as db, db:
                db.execute("INSERT INTO measurements VALUES (223)")
            raise OSError("injected receipt failure")

        with (
            patch.object(CUTOVER, "publish_version", side_effect=interrupt),
            self.assertRaisesRegex(OSError, "injected receipt failure"),
        ):
            CUTOVER.activate(self.paths, self.work, NEW, PHOTO, True)
        self.assertEqual(self.journal()["phase"], "candidate-may-have-run")
        CUTOVER.recover(self.paths, self.work)
        self.assertEqual(self.journal()["phase"], "verified")
        self.assertEqual(CUTOVER.read_pair(self.paths), NEW)
        self.assert_history([222, 223])

    def test_supervised_unverified_previous_receipt_blocks_before_stop(self):
        """Require a verified previous transaction before stopping it."""
        previous = self.existing_supervisor()
        prior = self.paths.home / previous["transaction_token"]
        CUTOVER.record(prior / "transaction.json", {"phase": "preboot"})
        with self.assertRaisesRegex(ValueError, "not verified"):
            CUTOVER.activate(self.paths, self.work, NEW, PHOTO, True)
        self.assertNotIn(("systemctl", "stop", CUTOVER.SUPERVISOR), self.calls)
        self.assert_history([222])

    def test_preboot_failure_restores_the_original_pair(self) -> None:
        """A failed switch repairs source, receipt, unit and restart mode."""
        self.failed_reload = True
        with self.assertRaisesRegex(ValueError, "injected reload failure"):
            CUTOVER.activate(self.paths, self.work, NEW, PHOTO)
        self.assertEqual(CUTOVER.read_pair(self.paths), OLD)
        self.assertEqual(self.paths.unit.read_text(), LEGACY_UNIT)
        self.assertFalse(self.paths.marker.exists())
        self.assertEqual(self.journal()["phase"], "recovered")
        self.assertEqual(
            self.runtime,
            (CUTOVER.image_pin(OLD["digest"]), "running", "unless-stopped"),
        )
        self.assertEqual(self.timer, "active")
        self.assert_history([222])

    def test_legacy_start_and_reload_are_guarded_before_marker(self) -> None:
        """Fence the old systemd unit before the cutover can be interrupted."""
        original = CUTOVER.make_marker

        def abort_after_marker(paths, work):
            helper = work / "boot-guard.sh"
            expected = f"/bin/bash {helper} --guarded-start"
            commands = CUTOVER.startup_commands(paths.unit.read_bytes())
            self.assertEqual(commands, CUTOVER.startup_pair(expected))
            self.assertNotIn(b"ExecStartPre", paths.unit.read_bytes())
            self.assertEqual(self.runtime[2], "no")
            self.assertTrue(helper.exists())
            original(paths, work)
            raise KeyboardInterrupt

        with (
            patch.object(
                CUTOVER, "make_marker", side_effect=abort_after_marker
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            CUTOVER.activate(self.paths, self.work, NEW, PHOTO)
        self.assertTrue(self.paths.marker.exists())
        CUTOVER.recover(self.paths, self.work)
        self.assertEqual(self.paths.unit.read_text(), LEGACY_UNIT)
        self.assert_history([222])

    def test_old_unit_returns_only_after_its_marker_is_cleared(self) -> None:
        """Never re-arm legacy automatic recovery while the marker exists."""
        saved = CUTOVER.capture(self.paths, self.work, NEW, PHOTO)
        CUTOVER.save_phase(self.work, saved, "preboot")
        CUTOVER.arm_boot_guard(self.paths, self.work)
        CUTOVER.freeze(self.paths, self.work, saved)
        original = CUTOVER.write_file

        def check_write(path, data, *args):
            if path == self.paths.unit and b"ExecStartPre" in data:
                self.assertFalse(self.paths.marker.exists())
            original(path, data, *args)

        with patch.object(CUTOVER, "write_file", side_effect=check_write):
            CUTOVER.recover(self.paths, self.work)
        self.assertEqual(self.paths.unit.read_text(), LEGACY_UNIT)
        self.assert_history([222])

    def test_unknown_existing_hook_blocks_cutover(self) -> None:
        """Do not run an operator hook before the pending-state guard."""
        self.paths.unit.write_text(
            LEGACY_UNIT + "ExecStartPre = /usr/bin/true\n"
        )
        with self.assertRaisesRegex(
            ValueError, "startup hook is unrecognized"
        ):
            CUTOVER.activate(self.paths, self.work, NEW, PHOTO)
        self.assertFalse(self.paths.marker.exists())
        self.assert_history([222])

    def test_candidate_must_not_run_hook_before_guard(self) -> None:
        """Reject a candidate unit with an independent startup command."""
        unit = self.work / "next" / "systemd" / CUTOVER.MONITOR
        unit.write_text(CURRENT_UNIT + "ExecCondition = /usr/bin/true\n")
        with self.assertRaisesRegex(ValueError, "startup guard"):
            CUTOVER.activate(self.paths, self.work, NEW, PHOTO)
        self.assertFalse(self.paths.marker.exists())
        self.assert_history([222])

    def test_supervised_candidate_blocks_before_any_runtime_change(self):
        """Do not stop a legacy writer for an incompatible signed release."""
        app = self.work / "next" / "app.py"
        app.write_text('raise WriterFenceError("SVT_FENCE_DIR_REQUIRED")\n')
        with self.assertRaisesRegex(ValueError, "requires the sole-writer"):
            CUTOVER.activate(self.paths, self.work, NEW, PHOTO)
        self.assertEqual(CUTOVER.read_pair(self.paths), OLD)
        self.assertEqual(self.paths.unit.read_text(), LEGACY_UNIT)
        self.assertEqual(self.timer, "active")
        self.assertFalse(self.paths.marker.exists())
        self.assertFalse(
            any(call[1] in {"stop", "update"} for call in self.calls)
        )
        self.assert_history([222])

    def test_recovers_interruption_between_source_renames(self) -> None:
        """The durable marker permits recovery with the live path absent."""
        saved = CUTOVER.capture(self.paths, self.work, NEW, PHOTO)
        CUTOVER.save_phase(self.work, saved, "preboot")
        CUTOVER.arm_boot_guard(self.paths, self.work)
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

    def test_supervisor_requires_the_protected_fence_mount(self) -> None:
        """A supervised image must share the exact host lock namespace."""
        data = ("/data", str(self.paths.database.parent), "bind", True)
        fence = (CUTOVER.FENCE_TARGET, str(self.paths.fence), "bind", True)
        CUTOVER.verify_database_mount([data, fence], self.paths, True)
        with self.assertRaisesRegex(ValueError, "protected writer fence"):
            CUTOVER.verify_database_mount([data], self.paths, True)

    def test_fence_helper_does_not_inherit_a_restrictive_umask(self) -> None:
        """Give the signed helper the mask needed for its 0755 directory."""
        saved = {"pair": NEW, "previous": OLD}
        with (
            patch.object(CUTOVER, "run") as run,
            patch.object(CUTOVER, "control_fence"),
            patch.object(CUTOVER, "remove_stopped_pair"),
        ):
            CUTOVER.arm_supervisor(self.paths, self.work, saved)
        self.assertIn("prepare-writer-fence.py", run.call_args.args[2])
        self.assertEqual(run.call_args.kwargs["umask"], 0o022)

    def test_supervisor_rejects_an_app_without_a_required_fence(self) -> None:
        """Setting a mount alone cannot make a legacy app use the fence."""
        source = self.work / "next"
        with self.assertRaisesRegex(ValueError, "does not require"):
            CUTOVER.require_runtime_owner(source, True)
        (source / "app.py").write_text('"SVT_FENCE_DIR_REQUIRED"\n')
        CUTOVER.require_runtime_owner(source, True)

    def test_remove_refuses_an_active_or_unrelated_container(self) -> None:
        """Handoff may remove only one of the exact stopped image pairs."""
        saved = {"previous": OLD, "pair": NEW}
        for image, status in (
            (CUTOVER.image_pin(OLD["digest"]), "running"),
            (CUTOVER.image_pin("sha256:" + "c" * 64), "exited"),
        ):
            with (
                patch.object(
                    CUTOVER, "run", return_value=CUTOVER.CONTAINER
                ) as run,
                patch.object(
                    CUTOVER, "docker_state", return_value=(image, status, "no")
                ),
                self.assertRaisesRegex(
                    ValueError, "unconfirmed stopped container"
                ),
            ):
                CUTOVER.remove_stopped_pair(saved)
            self.assertEqual(run.call_count, 1)

    def test_gate_releases_the_supervisor_after_the_legacy_marker(
        self,
    ) -> None:
        """Keep Docker fenced until the legacy pending marker is gone."""
        CUTOVER.make_marker(self.paths, self.work)
        self.paths.fence.mkdir()
        (self.paths.fence / ".svt-update-pending").write_text(
            self.work.name + "\n"
        )
        with patch.object(CUTOVER, "control_fence") as control:
            control.side_effect = lambda *_: self.assertFalse(
                self.paths.marker.exists()
            )
            CUTOVER.open_candidate_gate(
                self.paths, self.work, {"supervised": True}
            )
            control.assert_called_once_with(
                self.paths, self.work, "clear", self.paths.source
            )

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

    def test_public_directory_symlink_blocks_before_writer_stop(self):
        """Keep the original writer when DynamicUser owns the old alias."""
        old = self.existing_supervisor()
        private = self.paths.public_state.parent / "private"
        private.mkdir()
        marker = private / "status.json"
        marker.write_text("legacy state")
        self.paths.public_state.symlink_to(private, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "legacy DynamicUser"):
            CUTOVER.activate(self.paths, self.work, NEW, PHOTO, True)
        self.assertEqual(CUTOVER.read_pair(self.paths), old)
        self.assertFalse(self.paths.marker.exists())
        self.assertNotIn(("systemctl", "stop", CUTOVER.SUPERVISOR), self.calls)
        self.assertEqual(marker.read_text(), "legacy state")
        self.assert_history([222])

    def test_dangling_public_directory_symlink_is_rejected(self):
        """Do not treat a dangling link as an unprovisioned directory."""
        self.paths.public_state.symlink_to(
            self.paths.public_state.parent / "missing"
        )
        with self.assertRaisesRegex(ValueError, "directory is a symlink"):
            CUTOVER.public_receipt_preflight(self.paths)

    def test_public_receipt_symlink_blocks_before_writer_stop(self):
        """Never replace a public receipt link or touch its destination."""
        self.paths.public_state.mkdir()
        target = self.paths.state / "deployed.json"
        original = target.read_bytes()
        (self.paths.public_state / "deployed.json").symlink_to(target)
        with self.assertRaisesRegex(ValueError, "receipt is a symlink"):
            CUTOVER.activate(self.paths, self.work, NEW, PHOTO)
        self.assertEqual(target.read_bytes(), original)
        self.assertFalse(self.paths.marker.exists())
        self.assertNotIn(("systemctl", "stop", CUTOVER.MONITOR), self.calls)
        self.assert_history([222])

    def test_unsafe_public_directory_blocks_before_writer_stop(self):
        """Honor the protected-directory policy before touching a writer."""
        self.paths.public_state.mkdir()
        original = CUTOVER.safe_path.side_effect

        def reject_public(path, **kwargs):
            if path == self.paths.public_state:
                raise ValueError("protected path is unsafe")
            return original(path, **kwargs)

        with (
            patch.object(CUTOVER, "safe_path", side_effect=reject_public),
            self.assertRaisesRegex(ValueError, "protected path"),
        ):
            CUTOVER.activate(self.paths, self.work, NEW, PHOTO)
        self.assertEqual(CUTOVER.read_pair(self.paths), OLD)
        self.assertNotIn(("systemctl", "stop", CUTOVER.MONITOR), self.calls)
        self.assert_history([222])

    def test_missing_public_state_is_a_read_only_preflight(self):
        """A clean, absent directory is provisioned only at publication."""
        CUTOVER.public_receipt_preflight(self.paths)
        self.assertFalse(self.paths.public_state.exists())

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
