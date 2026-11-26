"""Exercise durable config-only recovery on disposable files."""

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/qa-report-mode-transaction.py"
)
SPEC = importlib.util.spec_from_file_location("mode_transaction", SCRIPT)
assert SPEC and SPEC.loader
TX = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TX)


class ReportModeTransactionTests(unittest.TestCase):
    """No Docker or production operations are performed."""

    def setUp(self):
        """Create a synthetic single-setting configuration."""
        scope = tempfile.TemporaryDirectory()
        self.addCleanup(scope.cleanup)
        self.root = Path(scope.name)
        self.env = self.root / ".env"
        self.env.write_bytes(TX.OLD)
        guard = patch.object(TX, "protected")
        guard.start()
        self.addCleanup(guard.stop)

    def test_prepare_does_not_change_mode(self):
        """Persist original bytes and the intended transition."""
        self.assertEqual(TX.prepare(self.root)["phase"], "prepared")
        self.assertEqual(self.env.read_bytes(), TX.OLD)
        self.assertEqual((self.root / "before.env").read_bytes(), TX.OLD)

    def test_recover_prepared_is_idempotent(self):
        """Complete the pause without claiming runtime acceptance."""
        TX.prepare(self.root)
        result = TX.recover(self.root)
        self.assertEqual(result["phase"], "configured")
        self.assertFalse(result["runtime_verified"])
        self.assertFalse(result["deployment_authorized"])
        self.assertEqual(TX.recover(self.root), result)
        self.assertEqual(self.env.read_bytes(), TX.NEW)

    def test_interrupt_after_config_write(self):
        """Recover if atomic config publication preceded journal update."""
        TX.prepare(self.root)
        with (
            patch.object(TX, "save_journal", side_effect=KeyboardInterrupt),
            self.assertRaises(KeyboardInterrupt),
        ):
            TX.recover(self.root)
        self.assertEqual(self.env.read_bytes(), TX.NEW)
        self.assertEqual(TX.read_journal(self.root)["phase"], "prepared")
        self.assertEqual(TX.recover(self.root)["phase"], "configured")

    def test_unknown_config_never_overwritten(self):
        """Fail closed on external configuration changes."""
        TX.prepare(self.root)
        self.env.write_bytes(b"foreign")
        with self.assertRaisesRegex(ValueError, "drift"):
            TX.recover(self.root)
        self.assertEqual(self.env.read_bytes(), b"foreign")

    def test_completed_config_drift_blocked(self):
        """Never silently replay after someone changes a completed mode."""
        TX.prepare(self.root)
        TX.recover(self.root)
        self.env.write_bytes(TX.OLD)
        with self.assertRaisesRegex(ValueError, "drifted"):
            TX.recover(self.root)
        self.assertEqual(self.env.read_bytes(), TX.OLD)

    def test_orphan_rescue_blocks_prepare(self):
        """A pre-journal crash is not proof of committed intent."""
        (self.root / "before.env").write_bytes(TX.OLD)
        with self.assertRaisesRegex(ValueError, "existing"):
            TX.prepare(self.root)
        self.assertEqual(self.env.read_bytes(), TX.OLD)

    def test_changed_rescue_blocks_recovery(self):
        """Require the original exact reviewed configuration."""
        TX.prepare(self.root)
        (self.root / "before.env").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "rescue changed"):
            TX.recover(self.root)

    def test_invalid_journal_blocks_recovery(self):
        """Do not accept arbitrary phases or hashes."""
        TX.prepare(self.root)
        (self.root / "transaction.json").write_text('{"phase":"verified"}')
        with self.assertRaisesRegex(ValueError, "unrecognized"):
            TX.recover(self.root)
        self.assertEqual(self.env.read_bytes(), TX.OLD)

    def fixture_container(self):
        """Model the exact stopped, isolated QA container."""
        return {
            "Id": "a" * 64,
            "State": {"Running": False},
            "Image": TX.QA_IMAGE,
            "Config": {"Env": ["SCHEDULED_REPORTS_MODE=paused"]},
            "HostConfig": {
                "NetworkMode": "none",
                "RestartPolicy": {"Name": "no"},
            },
            "Mounts": [
                {
                    "Destination": dest,
                    "Source": str(self.root / src),
                    "RW": True,
                    "Type": "bind",
                }
                for dest, src in (
                    ("/data", "data"),
                    ("/run/svt-fence", "fence"),
                )
            ],
        }

    def test_fenced_recovery_keeps_marker(self):
        """The shared marker survives until paused container review."""
        (self.root / "fence").mkdir()
        TX.prepare_fenced(self.root)
        TX.recover_fenced(self.root)
        self.assertEqual(
            TX.marker_path(self.root).read_text().strip(), self.root.name
        )
        with patch.object(
            TX, "inspect_qa_container", return_value=self.fixture_container()
        ):
            result = TX.arm_paused(self.root, "fixture")
        self.assertTrue(result["startup_gate_released"])
        self.assertFalse(result["runtime_verified"])
        self.assertFalse(TX.marker_path(self.root).exists())

    def test_foreign_marker_refused(self):
        """Do not clear or replace another update marker."""
        (self.root / "fence").mkdir()
        TX.marker_path(self.root).write_text("foreign")
        with self.assertRaisesRegex(ValueError, "existing startup"):
            TX.prepare_fenced(self.root)
        with self.assertRaisesRegex(ValueError, "foreign startup"):
            TX.recover_fenced(self.root)
        self.assertEqual(TX.marker_path(self.root).read_text(), "foreign")

    def test_orphan_marker_is_fail_closed(self):
        """A crash before journal publication cannot authorize startup."""
        (self.root / "fence").mkdir()
        TX.marker_module().publish(TX.marker_path(self.root), self.root.name)
        with self.assertRaises(FileNotFoundError):
            TX.recover_fenced(self.root)
        self.assertTrue(TX.marker_path(self.root).exists())

    def test_stale_enabled_container_keeps_marker(self):
        """A stale stopped container cannot release the startup fence."""
        (self.root / "fence").mkdir()
        TX.prepare_fenced(self.root)
        TX.recover_fenced(self.root)
        info = self.fixture_container()
        info["Config"]["Env"] = ["SCHEDULED_REPORTS_MODE=enabled"]
        with (
            patch.object(TX, "inspect_qa_container", return_value=info),
            self.assertRaisesRegex(ValueError, "unverified paused"),
        ):
            TX.arm_paused(self.root, "fixture")
        self.assertTrue(TX.marker_path(self.root).exists())

    def test_wrong_image_or_mount_blocks_arm(self):
        """Pin the image and the actual database and fence mounts."""
        info = self.fixture_container()
        info["Image"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(ValueError, "unverified paused"):
            TX.validate_paused(self.root, info)
        info = self.fixture_container()
        info["Mounts"][0]["Source"] = "/foreign"
        with self.assertRaisesRegex(ValueError, "unverified QA mount"):
            TX.validate_paused(self.root, info)

    def test_arm_replay_requires_same_container(self):
        """After marker removal only the exact durable ID is accepted."""
        (self.root / "fence").mkdir()
        TX.prepare_fenced(self.root)
        TX.recover_fenced(self.root)
        info = self.fixture_container()
        with patch.object(TX, "inspect_qa_container", return_value=info):
            first = TX.arm_paused(self.root, "fixture")
            self.assertEqual(TX.arm_paused(self.root, "fixture"), first)
            info["Id"] = "b" * 64
            with self.assertRaisesRegex(ValueError, "identity changed"):
                TX.arm_paused(self.root, "fixture")

    def test_interrupt_before_marker_clear_replays_exact_id(self):
        """A durable ID survives interruption while the fence remains."""
        (self.root / "fence").mkdir()
        TX.prepare_fenced(self.root)
        TX.recover_fenced(self.root)
        info = self.fixture_container()
        marker = TX.marker_module()
        with (
            patch.object(TX, "inspect_qa_container", return_value=info),
            patch.object(TX, "marker_module", return_value=marker),
            patch.object(marker, "clear", side_effect=KeyboardInterrupt),
            self.assertRaises(KeyboardInterrupt),
        ):
            TX.arm_paused(self.root, "fixture")
        self.assertTrue(TX.marker_path(self.root).exists())
        self.assertTrue((self.root / "armed-container.json").exists())
        with patch.object(TX, "inspect_qa_container", return_value=info):
            self.assertTrue(
                TX.arm_paused(self.root, "fixture")["startup_gate_released"]
            )

    def test_host_boundary(self):
        """Production cannot use the configuration-write prototype."""
        with (
            patch.object(TX.socket, "gethostname", return_value="prod"),
            self.assertRaisesRegex(ValueError, "isolated QA"),
        ):
            TX.require_qa(self.root)
