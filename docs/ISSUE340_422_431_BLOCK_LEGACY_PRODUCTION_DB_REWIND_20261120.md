# Issues 340/422/431: block legacy production SQLite rewind

Date: 2026-11-20 Europe/Kyiv
Scope: source-only QA; production scripts and running service not modified.

## Confirmed dangerous legacy path

The installed production v0.5.25-hw still has the old systemd
ExecStartPre apply-update.sh --recover hook and Docker with
restart policy unless-stopped. If a legacy pending-update marker
exists after an interrupted image change, recover_pending()
restores historical source/receipt/image and calls
restore_snapshot_database(). The old function deletes the current
SQLite -wal and -shm sidecars and replaces the active DB with an
older snapshot, which can silently discard newer L1/L2/L3
measurements, Telegram PHOTO identity or pending intents.

Even if a newer rescue file was created beforehand, a backup
file existing does not make history rollback safe or atomic.

## Source-only fail-closed protection

scripts/apply-update.sh now uses legacy_production_db_guard()
to canonicalize DATABASE_PATH and refuse paths under the
real production database directory /var/lib/shelly-voltage.
This explicit guard executes:

- before starting any non-dry-run old production update;
- when recover_pending() sees a pending update marker but BEFORE
  stopping Docker or touching the old image/SQLite files;
- before a directly sourced restore_snapshot_database call;
- before a directly sourced activate call can create a pending
  journal, change Compose or start an autonomous Docker writer.

No-pending legacy --recover remains a no-op, retaining the old
systemd boot path while the real supervisor has not been installed.
Disposable QA SQLite paths retain the existing isolated
interrupted-update tests. The old production update path is now
purposely unavailable until the actual signed sole-writer
transition protocol is accepted and deployed.

This must never be mistaken for an operational signed production
updater; it only prevents one unsafe legacy path in future source.

## QA evidence

- Isolated LXC114 production-path guard + old supervisor tests:
  35/35 PASS, Ruff0.14.2 and project quality gate PASS.
- No-pending startup returns 0 without Docker calls.
- Pending production recovery exits 3 BEFORE mock Docker and
  preserves the pending marker. Direct old DB restore and
  activation also exit 3 before any Docker call.
- Normalized production path with ../ is refused.
- QA private database control call remains permitted.
- No production file, SQLite/WAL/PHOTO, Telegram, Docker or
  systemd service was touched, restarted or updated.

## Important unresolved blockers

The live LXC110 currently uses the previously released script,
NOT this source-only guard, and still has an autonomous Docker
restart policy. Do not invoke the actual old --recover command
on production. A new signed source+OCI release, old/new release
receipts, newest SQLite rescue and a real supervised single-writer
activation/rollback are required before any production cutover.
