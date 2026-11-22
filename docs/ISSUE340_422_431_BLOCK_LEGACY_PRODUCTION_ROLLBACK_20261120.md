# Issue 340/422/431: refuse destructive legacy production rollback

Date: 2026-11-20 Europe/Kyiv
Scope: source-only QA. Production LXC110 was not changed.

## Confirmed unsafe legacy behavior

scripts/rollback.sh permits explicit --confirm-db-replacement and
can stop Compose, delete/restore installed sources, remove SQLite
-wal/-shm, and restore an old snapshot. An operator confirmation
for DB replacement is NOT proof that newer post-upgrade phase rows,
Telegram PHOTO/media identity and pending Telegram intents can be
safely discarded, or that no Docker unless-stopped writer can
restart outside the old systemd oneshot owner.

This legacy rollback must not operate on production paths until
a root-owned signed sole-writer cutover and full newest-history
recovery process is available.

## Source-only fail-closed guard

legacy_production_rollback_guard() canonicalizes both the
selected SQLite DB and source root with readlink -m.

It refuses any real restoration when the DB is in
/var/lib/shelly-voltage or the source root is /opt/shelly-voltage.

The guard runs before:
- the main path begins snapshot inspection and production actions;
- direct old stop_service() invokes Docker;
- direct restore_source() can remove installed source files;
- direct restore_database() can discard WAL;
- direct rescue_current_db() writes a checkpoint;
- direct start_service() can independently start Docker.

The --dry-run and --help read-only paths remain available.
No-start or confirm-db-replacement flags cannot waive the
production safety guard. QA-only source/DB directories retain
disposable legacy tests. Synthetic test calls of sourced
functions use a detached file without main() auto-execution.

## Isolated acceptance

- LXC114 52/52 focused rollback, legacy updater, signed image,
  YASNO Compose and interrupted-upgrade tests PASS.
- Production entrypoint refuses before selecting any snapshot.
- All directly sourced destructive operations refuse before
  a fake Docker call.
- Custom DB does not permit deletion of production source;
  custom source does not permit overwriting production DB.
- Normalized ../ aliases also refuse.
- Private synthetic QA paths remain eligible.
- Ruff0.14.2, format, shell Bash -n and quality gate PASS.

This is a protection only for the future release SOURCE. The
currently deployed production v0.5.25-hw still contains the
historical rollback command; do not execute it on LXC110.

## Remaining work

The disabled legacy production rollback is NOT replaced by an
operational signed updater. Still required: new signed source+
OCI identities, a trusted release receipt, a supervised
single-writer transaction and actual old/new WAL/PHOTO/Telegram
continuity across hard interruption and reboot in isolated QA.
