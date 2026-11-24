"""Exercise the signed cutover and its interruption points with real SQLite."""

import hashlib
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
            return str(self.paths.unit)
        if prop == "DropInPaths":
            return ""
        if prop == "UnitFileState":
            if self.supervisor_mode and unit != owner:
                return "disabled"
            return "enabled"
        if unit == owner:
            return "active" if self.runtime[1] == "running" else "inactive"
        return self.timer if unit == CUTOVER.PHOTO_TIMER else "inactive"

    def systemd(self, args: tuple[str, ...]) -> str:
        """Reject an unsafe startup even when the test injected an error."""
        owner = CUTOVER.SUPERVISOR if self.supervisor_mode else CUTOVER.MONITOR
        if args[1] == "show":
            return self.systemd_property(args[3], args[-1], owner)
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
