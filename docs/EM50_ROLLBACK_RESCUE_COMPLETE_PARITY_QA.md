# Enforce full-history parity for a new rollback rescue

## RED finding

A producer could return success with a valid restorable SQLite gzip
and a valid SHA256 manifest, while the rescued content did not
represent the current source database. A synthetic live SQLite with
one value 222 was paired with an older archive value 111; identical
row counts and quick_check=ok did not block rollback.

## Fail-closed comparison

The existing validate-sqlite-archive.py accepts a new optional
--compare-db SOURCE argument. After the bounded gzip restore and
SQLite integrity check, it opens the stopped source database
read-only and compares the complete logical SQL state using the
already-tested whole_sqlite_digest implementation from the project's
predeploy backup parity guard. Persistent user_version and
application_id header fields are included in that digest.

The rollback rescue path now requires --compare-db "$DB" before
source extraction or live SQLite replacement. The existing default
archive-validation CLI remains unchanged when no comparator is given.

A full-history mismatch causes nonzero exit and preserves both the
live source and database. This gate assumes that the writer has
already been verified stopped; it is not authorization to stop it.

## Tests and scope

The synthetic RED/GREEN fixture checks a valid but stale gzip with
identical row count; the invalid/corrupt SQLite fixture and the
legitimate same-second independent rescues remain required.
No production SQLite capture, database modification, Docker stop,
Telegram action, hardware control or deployment was performed.
