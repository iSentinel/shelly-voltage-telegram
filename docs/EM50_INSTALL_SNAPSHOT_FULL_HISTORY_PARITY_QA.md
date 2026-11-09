# Require full-history SQLite parity before installing updated source

## Reproduced P0 failure mode

The existing upgrade installer built a source+SQLite snapshot,
then verified only that the gzip could restore and SQLite
quick_check passed. A disposable producer exited successfully
and wrote a checksum-valid, restorable SQLite with identical row
count but a different historic value. The snapshot helper accepted
it (RED), potentially permitting source replacement without a
recoverable backup of the exact current history.

## Change

The archive validator now runs with --compare-db pointing to the
same SQLite source used by the backup writer, reusing the tested
whole_sqlite_digest comparator, covering tables, schema and
persistent user_version/application_id. Failure aborts before
copy_source, Docker update or retention rotation.

The installer never stops the writer itself. Its separate
existing-install guard requires a stopped Docker writer, but
release deployment still needs independent quiescence,
human source review and signed rollout/rollback approval.

## Disposable test

Source only the isolated snapshot helper, never main install.
A fake producer emits a checksum-valid, stale SQLite gzip.
Expect an error specifically identifying whole-history parity.
Regular same-second captures, VERSION input guards, private
snapshot root and rotation must continue to pass.

No production backup, Docker stop, installation, or Telegram action.
