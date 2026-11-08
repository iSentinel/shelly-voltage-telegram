# Nightly and rollback-rescue SQLite backup uniqueness

## Reproduced defect

The old backup helper named both the intermediate SQLite copy and
final compressed archive using a timestamp with second precision.
Two snapshots initiated within that second wrote the same filenames.
A synthetic pair with distinct SQLite history produced only one
backup (RED), destroying the earlier copy.

## Fail-closed approach

- Each run uses a private 0700 temporary staging directory created by
  mktemp inside the owner's 0700 backup destination.
- Each final archive has a random suffix in addition to the timestamp;
  existing consumers select with voltage-*.sqlite3.gz and remain
  compatible.
- The SQLite copy and gzip checksum are completed in the private stage.
  Hard links publish the finished gzip and exact-path SHA256 sidecar
  exclusively; existing archive paths cannot be overwritten.
- A failed checksum publication removes only the archive this process
  successfully linked. The private staging directory is removed on
  exit. No partial archive remains accepted as a verified backup.
- Owner-only file permissions (0600), strict retention count
  validation and existing unique backup rotation remain in effect.

## Synthetic tests

The same-second test restores both SQLite archives and checks SHA256,
permissions, independent data and no leftover staging directory.
Other negative tests cover failed sidecar publication, invalid
retention settings, and BACKUP_KEEP=1 rollback-rescue semantics.

This is candidate code in a Draft PR, not deployed to the nightly
production service. The current live-history predeploy backup remains
unmatched, and this improvement does not authorize a deployment.
