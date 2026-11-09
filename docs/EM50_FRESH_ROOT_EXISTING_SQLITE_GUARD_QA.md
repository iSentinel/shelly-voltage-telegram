# Reject unversioned new target when shared Shelly SQLite already exists

## Reproduced recovery gap

The installer previously identified a "fresh install" solely by the
absence of TARGET/VERSION and a stopped or absent named Docker writer.
It never checked the persistent database at
/var/lib/shelly-voltage/voltage.sqlite3. A brand-new root could
therefore launch against a previous installation's SQLite without
source+database migration or rollback snapshot.

The source+SQLite snapshot stage deliberately skips installs that
lack TARGET/VERSION, making this an unsafe implicit migration path.

## Fail-closed preflight

Before inspecting the writer, creating a snapshot or touching Docker,
require_safe_fresh_database_state checks for the existing database,
SQLite -wal, -shm and -journal sidecars, and dangling symlinks. An unversioned
target with any of those paths present is rejected with exit status 5.

A versioned target still uses the existing quiesced upgrade and
validated complete-history snapshot checks. Truly fresh installs
with no existing persistent SQLite remain possible. Dry-run also
reports the migration blocker rather than claiming success.

## Independent QA

- RED: seven assertions failed with the missing guard.
- GREEN: 4/4 new tests PASS covering database, -wal, -shm, -journal,
  symlink, truly empty state, versioned upgrade and stage ordering.
- LXC 110 installer fixture: FRESH_INSTALL_WITH_EXISTING_DB=BLOCKED;
  versioned in-place dry-run and two configuration fixtures PASS.
- Isolated Python 3.13 full test suite: 1338/1338 PASS.
- Ruff 0.14.2, project quality, Bash syntax PASS.

No production SQLite was opened for writing, no backup was
captured and no Docker, Telegram or Shelly operation performed.
Draft stacked on PR #290 with sibling #280 to be separately
integration-tested. The release remains BLOCKED until independent
human source review, quiesced exact-history snapshot and rollback,
protected YASNO setup, Telegram real-client visual acceptance and
signed deployment/rollback approval.
