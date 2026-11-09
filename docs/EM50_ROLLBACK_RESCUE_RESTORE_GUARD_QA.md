# Verify new rescue SQLite can restore before destructive rollback

## RED finding

After an apparently successful backup.sh exit, rollback accepted the
newly created rescue folder without independently verifying its gzip,
exact-path SHA256 sidecar, or SQLite contents. A synthetic backup writer
returned code zero and emitted a checksum-valid but invalid SQLite
gzip; rollback accepted it. This jeopardizes recovery of the current
live history if the following database replacement fails.

## Fix

After a rescue is created, but before source/database replacement:
1. Require exactly one regular rescue SQLite archive.
2. Verify the corresponding SHA256 sidecar is present, independent,
   non-symlinked and bound to the exact archive pathname.
3. Decompress under a bounded private temporary directory and perform
   SQLite quick_check plus required measurements-table validation.

Any invalid rescue archive fails closed. The original live database
and application source are not replaced. The archive-verifier is the
existing pinned validate-sqlite-archive.py helper, not a second
implementation of SQLite integrity logic.

## Acceptance scope

A temporary fake backup producer returns success with a checksum-valid
invalid SQLite archive. The test requires rollback rescue validation
to reject it. Two legitimate same-second rescue backups remain
independently restorable, while normal synthetic rollback and source
release/install tests still pass.

This is Draft source-only QA, with no production container stop,
database restore, Telegram send/edit, hardware control or release.
