# Hard-linked source files and private-data containment

## Finding and scope

A hard link into a private inode is still a regular file. Excluding the
original path (`secrets/`, `.env`, etc.) and rejecting symlinks does not
exclude a public-looking pathname pointing at the *same inode*. The
previous `source-tar.sh` emitted this data through `tar`, and
`release.sh` copied the same data via its initial tar staging operation.

A disposable synthetic private file is hard-linked as
`nested/public-looking.txt`. It contains only fake bytes and does not
reference any actual runtime secret. The previous source-tar helper
passed, which the new regression test reports as RED.

## Fail-closed source TAR

`scripts/source-tar.sh` now enumerates its filtered path list into a
private `mktemp` file, checks each included regular file for more than
one hard link, and emits no archive bytes unless all checks pass.
A private inode aliased into the published tree therefore causes a
generic error without exposing the source filename.

## Fail-closed public release

`scripts/release.sh` independently refuses candidate source trees
containing any hard-linked regular file before even a release
`--dry-run` can advertise success. This rule deliberately also rejects
hard-linked excluded files; it favors a conservative refusal over
incorrectly assuming an aliased inode is safe.

The synthetic fixtures require **both** packaging paths to reject
the aliased fake secret. Previous symlink, nested generated directory,
`.env.example` alias, unsafe VERSION, reproducibility and installer
fixtures remain part of CI.

## Limitations and release blockers

This is a static source policy, not an immutable filesystem snapshot.
Untrusted concurrent modification of the packaging worktree is out of
scope; real releases still need immutable pinned source and an
independent review. No production release is authorized. The live
SQLite writer, Docker container, Telegram and Shelly must not be
changed by these tests.
