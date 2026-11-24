"""Exercise updater failure handling without real Docker resources."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIGEST = "sha256:" + "a" * 64
MOCK_ACTIVATION = """
    source "$1"
    requirements() { mkdir -p "$STATE"; }
    preflight() { :; }
    storage_preflight() { :; }
    snapshot() { :; }
    verify_image() { :; }
    verify_source_archive() { :; }
    docker() { :; }
    python3() { if [[ "$2" == digest ]]; then echo test; fi; }
    activate() {
        echo test > "$PENDING_FILE"
        echo "$RECEIPT" > "$STATE/deployed.json"
        echo "image: $IMAGE@$PIN" > "$STATE/override.yaml"
        chmod 0600 "$STATE/deployed.json" "$STATE/override.yaml"
    }
    rollback() { echo 'UNEXPECTED ROLLBACK'; exit 19; }
    main --version 0.5.25-hw
"""


class OfflineRollbackTests(unittest.TestCase):
    """Keep real monitoring data out of rollback unit tests."""

    def setUp(self):
        """Create temporary updater state."""

        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_recovery_clears_public_version_without_deployment(self):
        """Restoring the original image clears stale version metadata."""

        state = self.root / "private"
        public = self.root / "public"
        state.mkdir()
        public.mkdir()
        (public / "deployed.json").write_text(
            '{"version":"0.5.13-hw"}', encoding="utf-8"
        )
        script = ROOT / "scripts" / "apply-update.sh"
        environment = {
            **os.environ,
            "SVT_UPDATE_STATE": str(state),
            "SVT_PUBLIC_STATE": str(public),
        }
        result = subprocess.run(
            [
                "bash",
                "-c",
                'source "$1"; sync_public_version',
                "bash",
                str(script),
            ],
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((public / "deployed.json").exists())

    def test_dry_run_cleans_workspace_and_exits_successfully(self):
        """A successful dry-run must not fail in its EXIT trap."""

        capture = self.root / "workspace_path"
        script = ROOT / "scripts" / "apply-update.sh"
        shell = "\n".join(
            (
                'source "$1"',
                "requirements() { :; }",
                "preflight() { :; }",
                "verify_image() { :; }",
                'verify_source_archive() { echo "$1" > "$CAPTURE"; }',
                'python3() { [[ "$2" != digest ]] || echo sha256:test; }',
                "main --version 0.5.12-hw --dry-run",
            )
        )
        env = {**os.environ, "CAPTURE": str(capture)}
        result = subprocess.run(
            ["bash", "-c", shell, "bash", str(script)],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DRY-RUN PASS", result.stdout)
        self.assertFalse(Path(capture.read_text().strip()).exists())

    def apply_with_mock_activation(self, state, public, pin=DIGEST):
        """Run the real updater finalization with a synthetic activation."""

        return subprocess.run(
            [
                "bash",
                "-c",
                MOCK_ACTIVATION,
                "bash",
                str(ROOT / "scripts/apply-update.sh"),
            ],
            capture_output=True,
            text=True,
            check=False,
            env={
                **os.environ,
                "SVT_UPDATE_STATE": str(state),
                "SVT_PUBLIC_STATE": str(public),
                "PIN": pin,
                "RECEIPT": json.dumps(
                    {
                        "version": "0.5.25-hw",
                        "digest": DIGEST,
                        "private": "test",
                    }
                ),
            },
        )

    def test_public_sync_failure_reports_partial_update(self):
        """A healthy activation can succeed before public publication fails."""

        state, public = self.root / "private", self.root / "public"
        public.mkdir()
        (public / "deployed.json").mkdir()
        result = self.apply_with_mock_activation(state, public)
        self.assertEqual(result.returncode, 4, result.stderr)
        self.assertIn("retry --sync-public-version", result.stderr)
        self.assertNotIn("UPDATE PASS", result.stdout)
        self.assertNotIn("UNEXPECTED ROLLBACK", result.stdout)
        self.assertFalse((state / "pending-update").exists())
        self.assertEqual(
            json.loads((state / "deployed.json").read_text())["version"],
            "0.5.25-hw",
        )
        self.assertEqual(list(public.glob(".deployed-*")), [])

    def test_success_requires_exact_public_receipt(self):
        """Success follows publication of a complete readable receipt."""

        state, public = self.root / "private", self.root / "public"
        public.mkdir()
        published = public / "deployed.json"
        published.write_text('{"version":"0.5.24-hw"}')
        result = self.apply_with_mock_activation(state, public)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("UPDATE PASS: 0.5.25-hw", result.stdout)
        self.assertEqual(
            json.loads(published.read_text()),
            {"version": "0.5.25-hw", "digest": DIGEST},
        )
        self.assertEqual(published.stat().st_mode & 0o777, 0o644)
        self.assertFalse((state / "pending-update").exists())

    def test_mismatched_pin_keeps_previous_public_version(self):
        """An inconsistent committed pair cannot publish an update PASS."""
        state, public = self.root / "private", self.root / "public"
        public.mkdir()
        report = public / "deployed.json"
        report.write_text('{"version":"0.5.24-hw"}')
        before = report.read_bytes()
        result = self.apply_with_mock_activation(
            state, public, pin="sha256:" + "b" * 64
        )
        self.assertEqual(result.returncode, 4)
        self.assertIn("DEPLOYED_PIN_MISMATCH", result.stderr)
        self.assertNotIn("UPDATE PASS", result.stdout)
        self.assertEqual(report.read_bytes(), before)

    def test_publication_cannot_replace_its_private_input(self):
        """An aliased public directory must leave the receipt protected."""
        state = self.root / "private"
        alias = self.root / "public"
        alias.symlink_to(state, target_is_directory=True)
        result = self.apply_with_mock_activation(state, alias)
        receipt = state / "deployed.json"
        self.assertEqual(result.returncode, 4)
        self.assertIn("public and private update state", result.stderr)
        self.assertEqual(receipt.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(receipt.read_text())["private"], "test")

    def test_new_update_cannot_replace_unresolved_recovery(self):
        """An unresolved journal survives a retry of the apply command."""

        state, public = self.root / "private", self.root / "public"
        state.mkdir()
        pending = state / "pending-update"
        pending.write_text("existing pending update\n")
        result = self.apply_with_mock_activation(state, public)
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("update recovery is pending", result.stderr)
        self.assertEqual(pending.read_text(), "existing pending update\n")
        self.assertFalse((state / "deployed.json").exists())

    def test_nested_failure_runs_rollback_handler(self):
        """An error in a nested health check must trigger recovery."""

        capture = self.root / "rollback_called"
        script = ROOT / "scripts" / "apply-update.sh"
        shell = "\n".join(
            (
                'source "$1"',
                "CHANGED=true",
                'recover_pending() { echo recovered >> "$CAPTURE"; }',
                'start_stack() { echo restarted >> "$CAPTURE"; }',
                "wait_healthy() { return 19; }",
                "activate() { wait_healthy; }",
                "runner() { trap rollback ERR INT TERM; activate; }",
                "if [[ 1 -eq 1 ]]; then runner; fi",
            )
        )
        result = subprocess.run(
            ["bash", "-c", shell, "bash", str(script)],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "CAPTURE": str(capture)},
        )
        self.assertEqual(result.returncode, 19)
        self.assertEqual(
            capture.read_text().splitlines(), ["recovered", "restarted"]
        )

    def test_cosign_version_gate(self):
        """Require compatible Cosign 3 before changing host state."""

        script = ROOT / "scripts" / "apply-update.sh"
        for version, accepted in (
            ("v3.0.6", True),
            ("devel", False),
            ("v2.5.0", False),
        ):
            command = (
                'source "$1"; '
                'test_version="$2"; '
                'cosign() { printf "GitVersion: %s\\n" "$test_version"; }; '
                "check_cosign_compatibility"
            )
            result = subprocess.run(
                ["bash", "-c", command, "bash", str(script), version],
                capture_output=True,
                text=True,
                check=False,
            )
            with self.subTest(version=version):
                self.assertEqual(result.returncode == 0, accepted)


if __name__ == "__main__":
    unittest.main()
