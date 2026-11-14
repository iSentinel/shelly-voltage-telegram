# Disposable QA maintenance-lock coordination for guarded timer

**QA ONLY; stacked on PR #349, which is stacked on PR #348.**
No production install, updater execution, database modification,
Telegram API access or internet connectivity is part of this change.

## Confirmed unsafe state on 2026-11-14

LXC114 is a separate Debian13 networkless QA guest. The fixed
signed v0.5.21-hw OCI runs only a synthetic app.Storage writer
and a disposable systemd timer, never the real Telegram process.

An exclusive lock was acquired on the private QA path:

    /var/lib/svt-qa-docker114/state/maintenance-owner.lock

With no pending marker yet created, the previous guarded timer
ignored the lock and restarted the previously stopped test writer:
app starts **12 -> 13** despite the simulated maintenance owner
retaining its exclusive lock. QA receipt exists under the private
LXC114 state directory. All QA services were then stopped/disabled
and the fake pending marker restored. No production data touched.

## QA-only candidate shared-lock protocol

The amended disposable helper opens **only** the fixed QA
maintenance lock using O_RDONLY | O_NOFOLLOW | O_CLOEXEC. It
requires a regular, root-owned, 0600, single-link inode, and
**never creates or truncates a lock file**.

It tries flock(LOCK_SH | LOCK_NB) and holds that shared lock
throughout the narrowly scoped evaluation and systemd restart.
If a simulated updater already holds an exclusive lock, it returns
SKIP_MAINTENANCE_LOCKED without reading the SQLite or starting
anything. If the file is missing, linked, misowned, or incorrectly
permissioned, the guard returns BLOCKED instead of guessing.

The lock file must be provisioned manually on the offline guest
before use, root owned, mode 0600. The timer unit's
ProtectSystem=strict remains enabled: the guard opens the lock
**read-only**, so it needs no new write exception to the sandbox.

## Actual independent LXC114 integration

- Before the fix, an exclusive QA maintenance lock with pending
  absent did NOT prevent the real timer from starting writer13.
- After the fix, an exclusive QA lock held for several timer ticks
  yielded SKIP_MAINTENANCE_LOCKED and writer remained stopped,
  app starts stayed 13 and SQLite quick_check=ok.
- When the fake updater released the exclusive lock, a later timer
  tick delegated a single systemd start: starts 13 -> 14.
  Subsequent tick returned SKIP_ALREADY_RUNNING. No second writer.
- The timer and Compose unit were disabled and the writer stopped.
  Docker restart=no and the synthetic pending marker were restored.
- Dedicated guarded-helper tests include lock absence, symlink,
  hardlink, wrong owner and permissions, exclusive contention,
  lock-held-across-systemctl and automatic release after errors.
  All **42/42** focused tests PASS and full project Ruff PASS.

## Important PRODUCTION NO-GO

The real installed manual updater and recovery hook currently use:

    /run/lock/shelly-voltage-updater.lock

The QA-only private lock above is **not** a replacement for that
production lock. No real updater or boot-hook source has been
changed or integrated with it.

**Deadlock warning:** do NOT hold a shared lock around systemctl
restart if the called Compose ExecStartPre tries to acquire an
exclusive lock on the *same inode*. That ordering can deadlock
(supervisor waits for systemd; prestart waits for supervisor's
shared lock). A reviewed production design needs a separate
coordination lock with a consistent acquisition order, or another
equivalent writer-fence protocol, plus bounded waits and recovery
of interrupted maintenance. The QA prestart does not take the
private maintenance lock and therefore does not prove that real
boot prestart and real updater can safely share one lock.

All real Docker/Compose/recovery/updater restart owners must be
fenced as a unit before current-history snapshot/rollback.
Live source v0.5.15-hw still differs from the running signed image
v0.5.21-hw. Fresh owner-approved writer-fenced full source+SQLite
A/B, physical power-loss durability, independent review and
explicit release approval remain separate gates. PHOTO remains NO_GO.

## Re-run / cleanup

Do not run this on any guest other than LXC114. The fixed helper is
inert without --run-disposable and the exact QA confirmation.

    python3 scripts/disposable-guarded-writer-restart-qa.py --dry-run

To simulate the updater, use only the private lock path above
and flock -x in a disposable QA shell. Keep the synthetic pending
marker absent only during that isolated test. Never use production
paths, credentials or live Docker Compose.

Always disable the QA timer, stop the QA Compose service and disable
its autostart after every test. Keep the test writer stopped with
Docker restart=no and the synthetic pending marker restored.
