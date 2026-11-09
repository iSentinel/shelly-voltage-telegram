# Preflight SQLite restore destination before Docker stop

## Reproduced availability risk

The rollback validator checked the SQLite file and parent symlinks,
but did not require the parent directory where mktemp creates the
restored database to exist. A synthetic --dry-run accepted a
DATABASE_PATH under a missing parent. In a real run, source cleanup
and the Docker stop could already have occurred when mktemp failed.

## Fail-closed change

Before stopping the writer, reject SQLite paths that are relative,
contain empty or dot/dot-dot components, or whose parent is absent,
not a directory, or unavailable for write/traversal. The previous
parent-symlink and hard-link checks remain mandatory. Do not change
the ownership of live SQLite directories; production can use
a dedicated uid and group, while rollback itself runs as root.

The isolated regression verifies rejection of missing parents,
a regular file in place of a directory, a relative SQLite path
and a path with dot-dot traversal. Each test leaves application
VERSION and synthetic current SQLite checksums unchanged.
Ordinary rollback, backup rescue, source TAR and install tests
remain covered.

No real Docker stop, backup capture, live database mutation,
Telegram send/edit or Shelly hardware action was performed.
