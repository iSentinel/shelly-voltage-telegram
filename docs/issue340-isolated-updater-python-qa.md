# Issue #340 — updater and timer Python startup isolation (QA ONLY)

**Stacked Draft on #395; NO_GO for production deployment.**

The legacy updater launched the Python release preflight, storage,
guard and digest CLI with ordinary `python3`; its embedded state
and rescue helpers used unisolated `python3 -`. Separately the
read-only systemd update-check timer used ordinary
`/usr/bin/python3 .../github_update.py check`.

The candidate invokes all Python entrypoints via
`/usr/bin/python3 -I`, including `-I -` for stdin scripts.
This forbids `PYTHONPATH` and current-directory module spoofing
before parsing any update arguments. It does **not** change approval,
signed manifest verification, production Docker, SQLite contents,
systemd unit installation, or the global owner freeze protocol.

The isolated tests verify exact call sites, safe `--help`, safe
synthetic stdin import, and an unsafe nonisolated negative control
with a forged `argparse.py`. No network release checking or
production updates are permitted during QA.

**Still NO_GO:** no complete old-owner G/B/W/A writer freeze,
approved pending boot, current signed OCI/source and two fresh
WAL-complete independently restored copies, real Telegram long-poll
teardown, physical blackout QA, independent security review and
explicit Owner Approval accepting a possible total single-NVMe loss.
Manual `python3` bypasses and piped/plain Bash require separate
controls. Public pinned PHOTO stays NO_GO.
