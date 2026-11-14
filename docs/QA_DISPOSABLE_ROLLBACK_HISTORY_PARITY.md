# Disposable SQLite rollback history parity audit

**QA-only; never a production rollback or maintenance approval.**

An installed Shelly Voltage updater recovery path can restore a
historical SQLite archive when a pending-update marker exists. Its
snapshot checksum and SQLite quick_check are necessary but do not,
by themselves, prove that the snapshot includes measurements received
after that backup was taken.

This audit is a safe, independent **test of that specific hazard**.
It accepts **only** private marked scratch directories under /tmp or
/var/tmp. It never opens a production database or source path.
The input names are fixed: current.sqlite3 and rollback.sqlite3.gz.

## Scratch-only use

Create a directory owned by the current user with permissions 0700.
Within it create:
- .disposable-rollback-history-qa containing exactly
  DISPOSABLE_SQLITE_HISTORY_ROLLBACK_QA followed by newline;
- current.sqlite3, owner-only 0600 synthetic SQLite;
- rollback.sqlite3.gz, owner-only 0600 synthetic gzip archive.

Only plain, one-link, owner-owned private input files are allowed.
Symlinks, hardlinks and unsafe paths fail closed. The archive is
bounded to 4 MiB compressed and 16 MiB restored (synthetic QA limits).

Safe default:

    python3 scripts/disposable-rollback-history-parity-qa.py \
        --root /var/tmp/my-private-qa-fixture --dry-run

Explicit disposable audit:

    python3 scripts/disposable-rollback-history-parity-qa.py \
        --root /var/tmp/my-private-qa-fixture --audit-disposable

Run -h or --help for all options. No Docker, systemd, Telegram or
production commands are ever issued by this program.

## Parity check

The tool verifies SQLite quick_check and the measurements table,
then hashes the **entire logical SQLite dump** plus user_version and
application_id on each side. It sees committed SQLite WAL data when
reading the synthetic current database.

An exact match returns exit 0 and status PARITY_PASS. A different
sample count, a changed voltage with the same row count, a changed
outage, metadata, schema or header returns exit 3 and BLOCKED.
Corrupt gzip and invalid paths return BLOCKED. Each output includes
production_modified=false and rollback_authorized=false.

The script creates a private, temporary restored SQLite only inside
the marked fixture, with a maximum expanded size, and deletes that
copy after checking. No live data is replaced under any condition.

## Acceptance evidence, November 14, 2026

27/27 targeted unit tests PASS with ResourceWarnings treated as errors;
full project-wide Ruff quality gate PASS.

Separate private live QA demonstration:
- synthetic current and archive each had 120 measurements;
- matching histories yielded PARITY_PASS but no authorization;
- one new measurement committed into the current database's WAL
  changed live to 121 while archive stayed at 120, yielding BLOCKED;
- private expanded copies and temporary files were removed.

Installed recovery implementation was inspected read-only. In the
installed apply-update.sh, recover_pending invokes
restore_snapshot_database, which checks the archive checksum and
SQLite quick_check then replaces the database after saving a rescue
copy. The inspected path does not compare entire archived and current
histories before replacement. **This is a production data-loss risk
to review, not proof of any actual data loss.** The exact pending
marker was absent at inspection; the live bot was online and healthy.

## Next production safeguards (not yet approved)

Future recovery should never overwrite new live measurements with
historical data merely because an archive has a valid SHA256. It
must guard the full current history under independently verified
writer quiescence and all reboot/restart owners, preserve a complete
current SQLite rescue copy, and fail closed when history has advanced.
Any recovery executor must be independently reviewed, tested on a
fully disposable replica and specifically authorized by the owner.

This QA tool must not be used to authorize deployment, replacement
of /var/lib/shelly-voltage/voltage.sqlite3, a source installation or
a Telegram TEXT-to-PHOTO change. Runtime signed image v0.5.21-hw
and installed host source v0.5.15-hw remain mismatched.
