"""Synthetic loaded-unit policy tests, not a real deployment receipt."""

import argparse
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from cutover_journal import JournalBlocked  # noqa: E402
from qa_observe_strict_unit import (  # noqa: E402
    HOOKS,
    check_fields,
    expected_commands,
    expected_unit,
)


class StrictUnitTests(unittest.TestCase):
    """Ensure only the staged, inactive, exact strict command is accepted."""

    def setUp(self):
        """Build one synthetic loaded unit."""
        self.args = argparse.Namespace(
            unit="svt-qa-test.service",
            container="svt-qa-test",
            identity="a" * 64,
            database=Path("/var/tmp/qa/history.sqlite3"),
            fence_dir=Path("/var/lib/qa"),
            source_version=Path("/qa/VERSION"),
        )
        start, stop = expected_commands(self.args)
        self.fields = {
            "LoadState": "loaded",
            "ActiveState": "inactive",
            "UnitFileState": "disabled",
            "NeedDaemonReload": "no",
            "FragmentPath": "/etc/systemd/system/" + self.args.unit,
            "DropInPaths": "",
            "Type": "simple",
            "Restart": "no",
            "Job": "",
            "User": "",
            "ExecStart": start,
            "ExecStop": stop,
            **dict.fromkeys(HOOKS, ""),
        }

    def check(self, fields):
        """Use already parsed commands to exercise policy."""
        check_fields(fields, self.args, lambda value: value)

    def test_exact(self):
        """The exact disabled unit passes structural checks only."""
        self.check(self.fields)

    def test_exact_file_contract(self):
        """The expected unit contains only the two allowed entrypoints."""
        text = expected_unit(self.args)
        start, stop = expected_commands(self.args)
        self.assertIn(f"ExecStart={start}\n", text)
        self.assertIn(f"ExecStop={stop}\n", text)
        for hook in HOOKS:
            self.assertNotIn(f"{hook}=", text)

    def test_hooks(self):
        """Refuse additional entrypoints and old reload paths."""
        for hook in HOOKS:
            with self.subTest(hook=hook), self.assertRaises(JournalBlocked):
                self.check(dict(self.fields, **{hook: "/bin/true"}))

    def test_owner_policy(self):
        """Refuse active, enabled, stale, or overridden units."""
        for key, value in (
            ("ActiveState", "active"),
            ("UnitFileState", "enabled"),
            ("NeedDaemonReload", "yes"),
            ("DropInPaths", "/etc/override"),
            ("Restart", "always"),
            ("Job", "42"),
            ("User", "nobody"),
        ):
            with self.subTest(key=key), self.assertRaises(JournalBlocked):
                self.check(dict(self.fields, **{key: value}))

    def test_strict_arguments(self):
        """Reject missing strict option, offline mode, or a mutable stop."""
        start = self.fields["ExecStart"]
        for key, value in (
            ("ExecStart", start.replace(" --require-pair-receipt", "")),
            ("ExecStart", start + " --qa-offline"),
            ("ExecStop", "/usr/bin/docker stop -t 10 svt-qa-test"),
        ):
            with self.subTest(key=key), self.assertRaises(JournalBlocked):
                self.check(dict(self.fields, **{key: value}))


if __name__ == "__main__":
    unittest.main()
