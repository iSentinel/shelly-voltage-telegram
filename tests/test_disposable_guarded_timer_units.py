"""Static fail-closed QA for systemd units dedicated to offline LXC114."""

from __future__ import annotations

import configparser
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UNIT_DIR = ROOT / "qa/systemd"
SERVICE_NAME = "svt-qa-guarded-watchdog.service"
TIMER_NAME = "svt-qa-guarded-watchdog.timer"
HELPER = "/var/lib/svt-qa-docker114/guarded-restart.py"
CONSENT = "DISPOSABLE_QA114_GUARDED_RESTART_ONLY"


def read_unit(filename: str) -> configparser.ConfigParser:
    """Load exactly one tracked QA unit without interpolation."""
    parser = configparser.ConfigParser(interpolation=None)
    with (UNIT_DIR / filename).open(encoding="utf-8") as stream:
        parser.read_file(stream)
    return parser


class DisposableGuardedTimerUnitsTests(unittest.TestCase):
    """Require QA-exclusive scope, no autonomous production startup."""

    @classmethod
    def setUpClass(cls) -> None:
        """Parse static text; do not call Docker or systemd."""
        cls.service = read_unit(SERVICE_NAME)
        cls.timer = read_unit(TIMER_NAME)

    def test_service_is_oneshot(self) -> None:
        """No long-running privileged service is installed."""
        self.assertEqual(self.service["Service"]["Type"], "oneshot")
        self.assertNotIn("Install", self.service)

    def test_service_fixed_helper_and_explicit_consent(self) -> None:
        """The only action is the narrowly scoped QA helper."""
        self.assertEqual(
            self.service["Service"]["ExecStart"],
            "/usr/bin/python3 "
            + HELPER
            + " --run-disposable --confirm "
            + CONSENT,
        )

    def test_service_requires_docker_before_qa_inspection(self) -> None:
        """A Docker socket must exist before checking test status."""
        self.assertEqual(
            self.service["Unit"]["Requires"],
            "docker.service",
        )
        self.assertEqual(self.service["Unit"]["After"], "docker.service")

    def test_service_requires_local_helper_path(self) -> None:
        """Missing QA source prevents the unit from executing."""
        self.assertEqual(
            self.service["Unit"]["ConditionPathExists"],
            HELPER,
        )

    def test_service_denies_new_privileges(self) -> None:
        """Avoid a new-privileges path from the test observer."""
        self.assertEqual(
            self.service["Service"]["NoNewPrivileges"],
            "yes",
        )

    def test_service_protects_host_fs(self) -> None:
        """No privileged service may write general host paths."""
        s = self.service["Service"]
        self.assertEqual(s["PrivateTmp"], "yes")
        self.assertEqual(s["ProtectSystem"], "strict")
        self.assertEqual(s["ProtectKernelTunables"], "yes")
        self.assertEqual(s["ProtectKernelModules"], "yes")
        self.assertEqual(s["ProtectControlGroups"], "yes")

    def test_service_allows_only_observation_socket_families(self) -> None:
        """Current network namespace inspection requires AF_NETLINK."""
        families = set(
            self.service["Service"]["RestrictAddressFamilies"].split()
        )
        self.assertEqual(families, {"AF_UNIX", "AF_NETLINK"})

    def test_service_has_bounded_execution(self) -> None:
        """A broken Docker daemon cannot hang a timer indefinitely."""
        self.assertEqual(
            self.service["Service"]["TimeoutStartSec"],
            "30",
        )
        self.assertNotIn("Restart", self.service["Service"])

    def test_timer_is_deliberately_short_for_qa_only(self) -> None:
        """Fast intervals must never be presented as production tuning."""
        t = self.timer["Timer"]
        self.assertEqual(t["OnActiveSec"], "4s")
        self.assertEqual(t["OnUnitActiveSec"], "6s")
        self.assertEqual(t["AccuracySec"], "1s")

    def test_timer_not_persistent_across_missed_ticks(self) -> None:
        """A boot should not replay past QA checks as a burst."""
        t = self.timer["Timer"]
        self.assertEqual(t["Persistent"], "false")
        self.assertEqual(t["RandomizedDelaySec"], "0")

    def test_timer_targets_one_exact_qa_service(self) -> None:
        """Never trigger any production Compose or updater unit."""
        self.assertEqual(self.timer["Timer"]["Unit"], SERVICE_NAME)
        self.assertEqual(self.timer["Install"]["WantedBy"], "timers.target")

    def test_timer_requires_local_test_helper(self) -> None:
        """Do not run a timer on a guest lacking the QA helper."""
        self.assertEqual(
            self.timer["Unit"]["ConditionPathExists"],
            HELPER,
        )

    def test_units_do_not_reference_production_paths(self) -> None:
        """No strings in the unit payload can target real monitoring."""
        content = "\n".join(
            (UNIT_DIR / name).read_text()
            for name in (SERVICE_NAME, TIMER_NAME)
        )
        for forbidden in (
            "shelly-voltage-bot",
            "/opt/shelly-voltage",
            "/var/lib/shelly-voltage",
            "shelly-voltage-compose.service",
            "shelly-voltage-updater",
            "telegram",
            "EnvironmentFile=",
            "ExecStartPre=/bin/bash",
        ):
            self.assertNotIn(
                forbidden,
                content.lower() if forbidden == "telegram" else content,
            )

    def test_units_have_no_network_or_exec_start_docker(self) -> None:
        """Unit must delegate to the hardened fixed-target helper."""
        s = self.service["Service"]
        self.assertNotIn("ExecStartPost", s)
        self.assertNotIn("ExecReload", s)
        self.assertNotIn("ExecStop", s)
        self.assertNotIn("/usr/bin/docker", s["ExecStart"])
        self.assertNotIn("Network", s)

    def test_qa_helper_dependency_exists_in_stacked_branch(self) -> None:
        """This QA package intentionally depends on PR348 helper."""
        self.assertTrue(
            (
                ROOT / "scripts/disposable-guarded-writer-restart-qa.py"
            ).is_file()
        )

    def test_documentation_disclaims_production_use(self) -> None:
        """Runbook must describe teardown and owner approval boundary."""
        text = (ROOT / "docs/QA_GUARDED_TIMER_LXC114.md").read_text()
        for required in (
            "QA ONLY",
            "systemctl disable --now",
            "LXC114",
            "845/845",
            "Telegram TEXT-to-PHOTO",
        ):
            self.assertIn(required, text)


if __name__ == "__main__":
    unittest.main()
