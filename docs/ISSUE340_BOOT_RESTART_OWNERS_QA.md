# Issue #340 — Read-only boot and restart ownership QA

## Scope

Production systemd service includes
ExecStartPre=/bin/bash /opt/shelly-voltage/scripts/apply-update.sh --recover,
but the repository copy of shelly-voltage-compose.service lacks
that prestart hook. Docker restart policy is independently
unless-stopped. The current pending-update marker is ABSENT.

This is a source/service drift and competing restart-owner concern,
not evidence of an actual boot failure. An absent pending marker is
not proof of recovery after a future reboot.

PR #336 already implements full logical SQLite history comparison
before and after stopping a writer. PR #424 checks signed image/source
pair identity and synthetic rollback failures. Neither supplies a
production-safe boot-continuity decision.

## Read-only audit

Run from the isolated QA worktree:

~~~bash
python3 scripts/boot-ownership-audit.py   --installed-unit /etc/systemd/system/shelly-voltage-compose.service   --source-unit "$PWD/shelly-voltage-compose.service"   --pending-marker /var/lib/shelly-voltage-updater/pending-update   --container shelly-voltage-bot --jsonl
~~~

The script reads the two regular unit files, marker existence and
Docker restart policy/running status through a bounded inspect
command. It also calls read-only systemctl show for the actual loaded
FragmentPath, DropInPaths, Type, LoadState, ActiveState, UnitFileState,
NeedDaemonReload and ExecStartPre/ExecStart/ExecStop commands. It refuses an unreviewed systemd drop-in, missing
or duplicated effective directives, ignored ExecStartPre errors and
loaded executable arguments that do not match the expected recovery
hook. A disabled unit, unloaded service or pending daemon-reload
also blocks any next-boot claim. This catches disk-vs-loaded unit
changes before a real restart. It never inspects environment variables or secrets and
never runs Docker start/stop, systemctl start/stop, updater --recover,
Telegram sends or SQLite writes.

Results:
- BLOCKED (exit 2): ambiguous or missing recovery hook, installed
  source drift, pending marker, competing Docker restart owner,
  unsafe file, unusual unit, or unavailable runtime evidence.
- PASS_STATIC_EVIDENCE_ONLY (exit 0): narrow static match only,
  NEVER proof of true boot continuity or authorization.

Both modes set boot_continuity_verified=false,
single_writer_fenced_verified=false, source_install_authorized=false,
rollback_authorized=false, production_modified=false.

## Read-only Gateway evidence 2026-11-18

- Actual unit SHA256:
  0d47b6b2757ec05d30cb1b4471a043a9eec32d2f50896b4209e7cb7a44779ef8.
- Repository unit SHA256:
  0b944ee5a807d1958e15d7c7fb848fcf17976a385e053810aab4f4db12841bee.
- Pending marker absent, Docker container running,
  Docker restart policy unless-stopped.
- Effective systemd: UnitFileState=enabled, LoadState=loaded,
  NeedDaemonReload=no. These are necessary checks, not boot approval.
- Effective systemctl show directives match the installed unit,
  DropInPaths is empty and no extra loaded start/recovery command exists.
- Exact result:
  BLOCKED BOOT_INSTALLED_SOURCE_DRIFT,
  BOOT_DOCKER_RESTART_OWNER_UNFENCED.
- During the owner-reported power outage, Docker process health
  was healthy but Shelly readiness returned 503 and samples were stale.

## Required next stages before any real boot or release change

1. Design one independently verified restart/fencing authority across
   Docker, systemd, Compose, updater and boot recovery.
2. Never replace newer SQLite history or WAL with an old snapshot.
   Preserve PHOTO identity, pending marker and diagnostics.
3. Prove a restricted continuation policy only on disposable
   systemd+Docker replicas, with stale/corrupt pending, SIGKILL,
   repeated boot and real isolated power-loss durability tests.
4. Demand a fresh writer-fenced complete-history rescue, exact
   signed OCI/source/receipt parity and no unresolved Telegram intent.
5. Obtain separate exact-head Owner Approval for any production
   configuration/restart and public Telegram changes.

The new installed but not configured three-phase Shelly and its
future owner-supplied IP are tracked independently under Issue #425.

## QA

~~~bash
python3 -W error::ResourceWarning -m unittest discover -s tests   -p test_boot_ownership_audit.py -v
scripts/quality.sh
~~~

## Disposable dual-owner contention rehearsal

Tests in tests/test_boot_dual_owner_rehearsal.py launch two separate
**plain Python mock owners** labelled docker and systemd. These are
not actual Docker/systemd instances or Shelly bot processes. Each
mock needs the same private POSIX flock token before inserting into
its generated SQLite. The mock checks pending-update marker presence
before acquiring the token. Tests cover:

- The first mock holding the token denies a simultaneous second
  writer, preserving PHOTO identity.
- Pending marker blocks either owner before SQLite mutation.
- SIGKILL releases the mock token but does not erase the pending gate.
- A new mock can resume after explicit simulated pending resolution;
  all existing measurements and PHOTO message ID survive.
- A linked lock path is rejected before a synthetic write.
- A pending marker appearing *after* boot acquires the fixture lock
  is detected by a second under-lock check before SQLite is opened.
  Even a dangling linked marker is rejected.
- A third ordinary Python fixture process represents an updater:
  it uses the **same** POSIX lock to create and fsync a pending marker.
  It cannot publish the marker while the mock bot owns the lock.
  Once that boot process is killed, the updater may publish the
  marker, but a subsequent boot remains blocked.

Both fixture types run without network access or user credentials.
The exercise is a *desired boot-policy contract*, not proof that
production implements this lock across Docker, systemd, Compose
and the update/rollback process. The pending marker creator must
participate in the **same fencing discipline**: the boot process's
double-check alone does not protect against a marker created
immediately after its second check by an unfenced updater. In particular it does NOT perform
a system reboot, a physical power-loss experiment, a real systemd
ExecStartPre execution or an actual source/OCI rollback.

~~~bash
python3 -W error::ResourceWarning -m unittest discover -s tests \
  -p test_boot_dual_owner_rehearsal.py -v
~~~
