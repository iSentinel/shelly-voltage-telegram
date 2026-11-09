# Rollback rejects aliased source and SQLite artifacts

## RED finding

The rollback verifier previously required regular non-symlink archives,
but a hard link appears as a regular file. A disposable rollback snapshot
was accepted when its source TAR had a second name outside the snapshot
directory. A matching SHA256 alone cannot establish that an artifact
has an independent inode.

## Fail-closed correction

Both selected archives (source.tar.gz and the unique voltage SQLite gzip)
and both matching *.sha256 sidecars must have link count exactly one.
A different hard-link count blocks rollback at archive validation,
before any Compose stop, SQLite rescue, source extraction or database
replacement. Existing checks for missing/symlinked manifests, exact-path
SHA256, size-limited SQLite restore, TAR path traversal, and source
completeness remain in place.

## Regression

The isolated rollback fixture creates four independent variations:
source TAR, SQLite gzip, source SHA256 sidecar, and SQLite SHA256 sidecar.
For each, a synthetic valid archive is replaced by a hard link to
another disposable path, keeping checksum contents correct. Each
case must be rejected and preserve the original target/SQLite hashes.

The test does not grant release approval, prove immutability against
concurrent hostile filesystem changes, or touch production backups,
Docker, SQLite writer, Shelly or Telegram.
