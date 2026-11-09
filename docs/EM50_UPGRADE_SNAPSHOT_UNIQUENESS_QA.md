# Preserve independent source and SQLite upgrade snapshots

## Reproduced data-loss risk

The installer originally named each upgrade snapshot directory with
only a version and second-resolution timestamp. Two synthetic captures
within one second reused the same folder: the second source tar
replaced the first, while BACKUP_KEEP=1 removed the earlier SQLite
archive. RED testing preserved only the second version.

## Candidate correction

- Reject malformed old VERSION metadata before constructing any path.
- Reject symlinks in the upgrade snapshot root and its ancestors.
- Make the upgrade root owner-owned 0700 (including existing roots).
- Allocate unique private snapshot folders using mktemp -d.
- Preserve source TAR + SQLite archive verification before rotation.
- Rotate at most five newest snapshots using NUL-separated safe paths.

The test redirects all fixed installer snapshot and SQLite paths into
a disposable sandbox, freezes time and calls ONLY the snapshot helper
on two independent temporary SQLite histories. No install, service
stop, live backup capture, Telegram or hardware commands execute.

This is Draft-only source hardening. Deployment still requires a
whole-history parity-matched snapshot, independent review, final
Telegram visual acceptance and explicit signed release/rollback.
