# Preflight rollback rescue and live SQLite paths before writer stop

## RED finding

The rollback procedure stopped the Docker writer before calling
backup.sh to capture an existing live SQLite database. If RESCUE_ROOT
contained a symlink in its directory ancestry, backup.sh correctly
rejected that path, but only after Docker was stopped. An identical
ordering problem affected DATABASE_PATH reached through a symlink
in its parent directory.

A disposable test with mocked Docker proves RED by recording a
stop-call marker. No production service is invoked.

## Fail-closed fix

Before stopping Compose or creating any rescue file, rollback validates:
- no symlinks anywhere in the SQLite or rescue destination ancestry;
- an existing SQLite source is a regular independent (one-link) file;
- an existing rescue destination is an owner-owned 0700 directory.

On any unsafe path, rollback exits with a generic diagnostic and does
not invoke Docker stop, alter current SQLite, extract a source TAR or
publish a partial rescue archive. The normal missing private rescue
directory can be created later by the approved rollback workflow.

## Synthetic acceptance

Both linked_rescue and linked_live_db fixtures must fail without
creating a Docker stop marker; source and SQLite SHA256 remain
unchanged. Existing tar/checksum validation and deliberately
stopped-writer rollback success are retained.

This documentation does not approve any production rollback, source
merge, restore of old SQLite history or release to 0.5.14-hw.
