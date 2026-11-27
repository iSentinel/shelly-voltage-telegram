"""Test fail-closed binding of independently pinned offline QA evidence."""

import copy
import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "bound_pair",
    Path(__file__).resolve().parents[1] / "scripts/qa-bound-offline-pair.py",
)
assert SPEC and SPEC.loader
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)


class BoundPairTests(unittest.TestCase):
    """Use synthetic evidence; real cryptographic review is separate."""

    def setUp(self):
        """Set exact synthetic pair and restrictive host facts."""
        self.root = Path("/var/lib/svt-report-mode-tx-qa.synthetic")
        self.policy = {
            "pair": {
                "version": "0.5.99-hw",
                "source_commit": "b" * 40,
                "digest": "sha256:" + "a" * 64,
                "source_sha256": "c" * 64,
            }
        }
        self.evidence = {
            "schema": 1,
            "version": "0.5.99-hw",
            "commit": "b" * 40,
            "index_digest": "sha256:" + "a" * 64,
            "source_sha256": "c" * 64,
            "source_signature_verified": True,
            "oci_signature_verified": True,
            "oci_attestations_verified": True,
            "deployment_authorized": False,
        }
        self.info = {
            "Config": {"User": "10001:10001"},
            "HostConfig": {
                "Privileged": False,
                "ReadonlyRootfs": True,
                "CapDrop": ["ALL"],
                "CapAdd": None,
                "SecurityOpt": ["no-new-privileges"],
                "Devices": [],
                "DeviceRequests": None,
                "PortBindings": {},
                "PidMode": "",
                "IpcMode": "private",
                "PublishAllPorts": False,
            },
        }

    def check(self, anchor=None):
        """Review exact evidence bytes with an optional distinct anchor."""
        raw = json.dumps(self.evidence).encode()
        with patch.object(QA.PAIR, "private_bytes", return_value=raw):
            QA.check_online(
                self.root, self.policy, anchor or QA.PAIR.digest(raw)
            )

    def test_exact_online_evidence(self):
        """Correct full identity and flags match."""
        self.check()

    def test_wrong_anchor(self):
        """The evidence cannot supply its own trust anchor."""
        with self.assertRaisesRegex(ValueError, "anchor mismatch"):
            self.check("f" * 64)

    def test_wrong_full_digest(self):
        """Matching suffix alone is insufficient."""
        self.evidence["index_digest"] = "sha256:" + "b" * 52 + "a" * 12
        with self.assertRaisesRegex(ValueError, "pair mismatch"):
            self.check()

    def test_unsupported_schema(self):
        """A boolean is not a valid schema version."""
        self.evidence["schema"] = True
        with self.assertRaisesRegex(ValueError, "schema mismatch"):
            self.check()

    def test_incomplete_verification(self):
        """Require exact true verification flags and false authorization."""
        for key, value in (
            ("source_signature_verified", False),
            ("oci_signature_verified", 1),
            ("oci_attestations_verified", False),
            ("deployment_authorized", True),
        ):
            old = self.evidence[key]
            self.evidence[key] = value
            with self.assertRaisesRegex(ValueError, "pair mismatch"):
                self.check()
            self.evidence[key] = old

    def test_restrictive_host(self):
        """Accept the exact synthetic security profile."""
        QA.check_host(self.info)

    def test_unsafe_host(self):
        """Reject privilege, capabilities, devices and namespace changes."""
        for key, value in (
            ("Privileged", True),
            ("ReadonlyRootfs", False),
            ("CapAdd", ["SYS_ADMIN"]),
            ("CapDrop", []),
            ("SecurityOpt", []),
            ("Devices", [{}]),
            ("DeviceRequests", [{}]),
            ("PortBindings", {"80/tcp": [{}]}),
            ("PidMode", "host"),
            ("IpcMode", "host"),
            ("PublishAllPorts", True),
        ):
            info = copy.deepcopy(self.info)
            info["HostConfig"][key] = value
            with self.assertRaisesRegex(ValueError, "unsafe offline"):
                QA.check_host(info)
        self.info["Config"]["User"] = "0"
        with self.assertRaisesRegex(ValueError, "unsafe offline"):
            QA.check_host(self.info)
