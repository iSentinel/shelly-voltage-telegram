"""Validate the offline signed-pair review against synthetic identities."""

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "pair_policy",
    Path(__file__).resolve().parents[1]
    / "scripts/qa-report-signed-pair-policy.py",
)
assert SPEC and SPEC.loader
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)


class SignedPairPolicyTests(unittest.TestCase):
    """Use only fake receipt proofs; test real fingerprint comparisons."""

    def setUp(self):
        """Construct a private synthetic installed pair and stopped writer."""
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.addCleanup(patch.stopall)
        patch.object(QA.TX, "protected").start()
        patch.object(QA.SUP, "require_root_ancestors").start()
        patch.object(
            QA.SUP, "source_version", return_value="0.5.99-hw"
        ).start()
        self.make_pair()
        self.make_container()
        self.write_policy()

    def write(self, name, data):
        """Write a private fixture input."""
        path = self.root / name
        path.write_bytes(data)
        path.chmod(0o600)
        return data

    def make_pair(self):
        """Supply fake proof fields and actual hashed fixture archive bytes."""
        archive = self.root / "release-archive"
        archive.mkdir(mode=0o700)
        raw = self.write(
            "release-archive/shelly-voltage-telegram-0.5.99-hw.tar.gz",
            b"synthetic archive",
        )
        self.pair = {
            "version": "0.5.99-hw",
            "digest": "sha256:" + "a" * 64,
            "source_commit": "b" * 40,
            "source_sha256": QA.digest(raw),
            "transaction_token": "cutover-synthetic",
        }
        self.record = {
            **self.pair,
            "commit": self.pair["source_commit"],
            "archive_sha256": self.pair["source_sha256"],
            "pair_status": "PASS_SIGNED_SOURCE_OCI_PAIR_ONLY",
            "source_signature_verified": True,
            "oci_signature_verified": True,
            "oci_attestations_verified": True,
        }
        self.raw = self.write(
            "deployed.json", json.dumps(self.record).encode()
        )
        self.env = self.write(".env", b"SCHEDULED_REPORTS_MODE=paused\n")

    def make_container(self):
        """Model the exact local image and networkless two-mount fixture."""
        self.pin = QA.SUP.IMAGE + self.pair["digest"]
        self.info = {
            "Id": "c" * 64,
            "Image": "sha256:" + "d" * 64,
            "Config": {
                "Image": self.pin,
                "Env": ["SCHEDULED_REPORTS_MODE=paused"],
                "Labels": {"org.opencontainers.image.revision": "b" * 40},
            },
            "State": {"Running": False},
            "HostConfig": {
                "NetworkMode": "none",
                "RestartPolicy": {"Name": "no"},
            },
            "Mounts": [
                {
                    "Destination": target,
                    "Source": str(self.root / name),
                    "Type": "bind",
                    "RW": True,
                }
                for target, name in (
                    ("/data", "data"),
                    ("/run/svt-fence", "fence"),
                )
            ],
        }
        self.image = {
            "Id": self.info["Image"],
            "RepoDigests": [self.pin],
            "Config": {"Labels": self.info["Config"]["Labels"]},
        }

    def write_policy(self):
        """Bind the fake receipt and current effective configuration."""
        self.policy = {
            "schema": 1,
            "pair": self.pair,
            "receipt_sha256": QA.digest(self.raw),
            "configuration_sha256": QA.digest(self.env),
            "container_id": self.info["Id"],
            "image_id": self.info["Image"],
            "container_config_sha256": QA.config_digest(self.info),
            "mode": "paused",
            "deployment_authorized": False,
        }
        self.write("signed-policy.json", json.dumps(self.policy).encode())

    def review(self):
        """Run the real review without Docker or production data."""
        return QA.review(self.root, self.info, self.image)

    def test_synthetic_pair_matches_without_authority(self):
        """Matching inputs do not establish a new signature or permission."""
        result = self.review()
        self.assertEqual(result["status"], "PASS_QA_PAIR_BINDING_ONLY")
        for key in (
            "startup_authorized",
            "deployment_authorized",
            "signatures_reverified",
        ):
            self.assertIs(result[key], False)

    def test_changed_receipt(self):
        """Exact proof bytes remain pinned even with unchanged semantics."""
        self.write("deployed.json", self.raw + b" ")
        with self.assertRaisesRegex(ValueError, "receipt bytes changed"):
            self.review()

    def test_pair_fields_must_match_even_with_new_receipt_hash(self):
        """A policy for another transaction cannot review the current pair."""
        self.policy["pair"] = dict(
            self.pair, transaction_token="cutover-foreign"
        )
        self.write("signed-policy.json", json.dumps(self.policy).encode())
        with self.assertRaisesRegex(ValueError, "signed pair changed"):
            self.review()

    def test_receipt_aliases_cannot_disagree(self):
        """Reject internally inconsistent aliases even if receipt is pinned."""
        self.record["commit"] = "e" * 40
        self.raw = self.write(
            "deployed.json", json.dumps(self.record).encode()
        )
        self.write_policy()
        with self.assertRaisesRegex(ValueError, "receipt aliases disagree"):
            self.review()

    def test_changed_archive(self):
        """A changed retained archive cannot match the receipt."""
        self.write(
            "release-archive/shelly-voltage-telegram-0.5.99-hw.tar.gz",
            b"other",
        )
        with self.assertRaisesRegex(ValueError, "ARCHIVE_MISMATCH"):
            self.review()

    def test_changed_configuration(self):
        """Private configuration drift cannot use the old policy."""
        self.write(".env", b"SCHEDULED_REPORTS_MODE=enabled\n")
        with self.assertRaisesRegex(ValueError, "configuration changed"):
            self.review()

    def test_changed_container(self):
        """Replacement identity, running state and extra mounts fail."""
        original = copy.deepcopy(self.info)
        for key, value in (("Id", "e" * 64), ("State", {"Running": True})):
            self.info = copy.deepcopy(original)
            self.info[key] = value
            with self.assertRaisesRegex(
                ValueError, "container policy changed"
            ):
                self.review()
        self.info = original
        self.info["Mounts"].append(
            {
                "Destination": "/app",
                "Source": "/tmp",
                "Type": "bind",
                "RW": False,
            }
        )
        with self.assertRaisesRegex(
            ValueError, "unexpected QA container mounts"
        ):
            self.review()

    def test_changed_effective_environment(self):
        """Container config must match exactly, independent of .env."""
        self.info["Config"]["Env"].append("SCHEDULED_REPORTS_MODE=enabled")
        with self.assertRaisesRegex(ValueError, "container policy changed"):
            self.review()

    def test_local_image_without_digest(self):
        """Revision and image ID alone are not sufficient evidence."""
        self.image["RepoDigests"] = []
        with self.assertRaisesRegex(ValueError, "OCI identity unconfirmed"):
            self.review()

    def test_changed_image_id(self):
        """Registry digest cannot substitute for the expected image ID."""
        self.image["Id"] = "sha256:" + "f" * 64
        with self.assertRaisesRegex(ValueError, "OCI identity unconfirmed"):
            self.review()

    def test_duplicate_json_keys(self):
        """Reject duplicates before interpreting policy or receipt fields."""
        with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
            QA.object_bytes(b'{"pair":{"version":"a","version":"b"}}')

    def test_schema_rejects_authority_and_legacy(self):
        """Reject unknown or boolean schema and purported authorization."""
        for key, value in (
            ("schema", 2),
            ("schema", True),
            ("deployment_authorized", True),
        ):
            policy = dict(self.policy, **{key: value})
            with self.assertRaisesRegex(ValueError, "unsupported"):
                QA.validate_policy(policy)
