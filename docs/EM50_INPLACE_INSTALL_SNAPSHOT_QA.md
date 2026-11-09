# Protect in-place reinstall with source and SQLite snapshot

## Reproduced recovery gap

When `SOURCE == TARGET`, the installer previously skipped
`snapshot_upgrade`, even though an in-place reinstall still executes
configuration, systemd service setup and Docker image build/start.
A root-owned disposable install dry-run demanded a stopped SQLite
writer but did not plan any source+SQLite recovery snapshot.

The RED regression test exercised the actual snapshot helper on an
isolated application root and found zero backup archives.

## Fix

Do not skip an existing versioned installation's snapshot merely
because the invoked installer source directory equals the destination.
The routine archives the installed application sources and uses the
existing SQLite backup and complete-history parity validator before
the in-place reinstall proceeds. New/fresh installs without VERSION
continue to skip upgrade snapshots.

## Acceptance evidence

- RED: in-place snapshot helper exited successfully with no backup.
- GREEN: source archive and gzip-compressed SQLite history created,
  restored and compared in disposable fixture.
- Targeted snapshot tests: 5/5 PASS.
- Isolated Python 3.13 full test suite: 1321/1321 PASS.
- Ruff 0.14.2, project quality, Bash syntax and 4/4 installer
  dry-run fixtures PASS.

**Not a production release.** Draft stacked on PR #284, with
sibling PR #280 to be independently integration-tested. Require
review approval, stopped-writer exact SQLite parity, protected
YASNO recipient, Telegram real-client visual approval and signed
rollout/rollback. Production Docker, SQLite and Shelly unchanged.
