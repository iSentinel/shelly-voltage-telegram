# Preserve independent rollback rescue copies

## Reproduced data-loss risk

The prior rollback rescue path was timestamped only to whole seconds.
Two rescue captures within the same second reused one directory.
The rescue backup writer uses BACKUP_KEEP=1; the second capture
therefore rotated away the first copy. A disposable SQLite test
reproduced RED: values 111 and 222 were recorded separately, but
only 222 remained restorable after two rescues.

## Fix

Each real rescue operation first validates its private root and
creates a distinct owner-only directory using mktemp -d and a
random suffix. Its timestamp remains human-readable, but the path
cannot collide with an earlier rescue, regardless of clock accuracy.
The 0700 root permission check fails closed; --dry-run displays
the proposed unique-pattern path without allocating directories.

A dedicated regression uses two distinct disposable SQLite snapshots,
a fixed fake clock and two isolated calls of rescue_current_db.
The test requires both values to survive in separate gzip archives.
Existing emergency rollback and strict archive integrity tests
must remain green.

This protects future approved rollbacks only. It does not modify
production, approve replacing live SQLite history, or unblock
0.5.14-hw deployment.
