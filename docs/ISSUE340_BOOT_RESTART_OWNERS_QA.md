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
NeedDaemonReload, After, Requires, Wants, and
ExecStartPre/ExecStart/ExecStop commands. It refuses an unreviewed systemd drop-in, missing
or duplicated effective directives, ignored ExecStartPre errors and
loaded executable arguments that do not match the expected recovery
hook. A disabled unit, unloaded service or pending daemon-reload
also blocks any next-boot claim. Effective Requires and After must
include docker.service; effective After and Wants must include
network-online.target. These are ordering/activation prerequisites,
not proof of safe actual Docker restart ownership. This catches disk-vs-loaded unit
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
  NeedDaemonReload=no; Requires/After include docker.service;
  After/Wants include network-online.target. Necessary checks only,
  not boot approval.
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

The new Pro3EM-3CT63 at 192.168.131.176 has since passed owner-approved
physical acceptance and is in production. Issue #425 is complete;
this boot-owner gate still applies to all three phases.

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

## Real isolated LXC114 Docker/systemd experiment, 2026-11-18

LXC114 is a disposable, offline QA guest: hostname
shelly-docker-reboot-qa, no network interface beyond loopback, no
production mounts, and a single synthetic Storage writer under a
private data folder. No current Shelly, production bot, Telegram or
production SQLite was present.

With the existing QA pending-update marker, an actual
systemctl start svt-qa-compose.service returned exit 1 as required
by ExecStartPre. Docker remained stopped, the synthetic SQLite stayed
at 876 records, and the marker survived. The unit was reset to
inactive/disabled.

The independent restart-owner failure was then reproduced by
temporarily changing only this synthetic container from restart=no
to unless-stopped, explicitly starting it and restarting the
**LXC114 Docker daemon**. Docker autorestarted the synthetic writer
despite the still-present pending-update marker. A subsequent
systemctl start again returned exit 1, yet the writer was already
running and 11 new synthetic records had appeared (876 -> 887).
This is a real Docker-daemon restart, not an NVMe/power-loss reboot.

Finally the synthetic container was stopped, restart=no restored,
the QA systemd unit left inactive/disabled and the pending marker
preserved. The isolated QA database intentionally retains the 887
synthetic records. Issue #340 was updated with exact observations:
https://github.com/iSentinel/shelly-voltage-telegram/issues/340

These experiments prove the particular bypass independently of any
prestart unit syntax check. They DO NOT authorize any changes to
production, demonstrate physical reboot durability or validate the
full SQLite/WAL restore transaction.

## Proposed future single-owner acceptance contract (not deployed)

- Exactly one restart authority may start the monitored writer.
  Docker cannot have an independent automatic restart policy that
  bypasses the chosen systemd guard. Any systemd watchdog or
  restart timer must perform that same guard on each attempt.
- Each boot, restart, updater and rollback path must share a
  verified writer-fencing protocol, with pending-update checked
  while holding the relevant lock before opening SQLite.
  The updater must use the same fencing discipline before it
  publishes the pending marker. Never auto-restore an old snapshot.
- Installed and tracked effective systemd unit commands must match,
  with required Docker and network dependencies. The currently
  installed apply-update.sh --recover hook is not an approved
  automated recovery plan: newest Pro3EM L1/L2/L3 history and saved
  meter selection require separate whole-database parity acceptance.
- A disposable real systemd+Docker test must prove that a stale or
  corrupt pending marker prevents *all* writers, even when Docker
  restarts independently, without falsely creating zero-voltages
  or publishing duplicate Telegram status messages.
- Add SIGKILL and actual isolated reboot tests; preserve SQLite
  history/WAL, PHOTO marker, pending intents, and signed
  OCI/source/receipt version parity across retries. Verify this
  before presenting an exact candidate for Owner Approval.
- A fail-closed design without a supervised recovery path may
  compromise availability. Supervision itself must never bypass
  a pending-update fence.

No production restart policy, systemd hook, updater timer, image,
signed version or Telegram channel message was changed by this QA.

## Real guest reboot A/B acceptance, 2026-11-18

Unlike the prior Docker-daemon restart test, this pair rebooted
**only disposable Proxmox LXC114** using pct reboot 114. The guest
remains offline with no production mounts, and every test used a
synthetic app.Storage writer under a private QA directory.

With a pending-update marker present, an enabled guarded systemd
unit and Docker restart=no:

- After reboot, ExecStartPre returned its pending-marker refusal.
  The real systemd unit failed closed and Docker never started.
- Synthetic SQLite remained **900 of 900 rows**. The deterministic
  complete logical SQLite dump SHA256 was identical before/after.
  Its synthetic writer-start counter remained **17**.
- The pending marker survived the reboot. The unit was returned
  to inactive/disabled and the Docker policy stayed no.

A second QA-only guest reboot deliberately reproduced the defect:
the same container was temporarily set to Docker
restart=unless-stopped, explicitly started before reboot, and the
systemd unit was enabled with pending-update still present.

- After reboot, guarded ExecStartPre again correctly refused start.
- Nevertheless Docker started the mock writer *independently*.
  Synthetic SQLite grew **900 -> 984 rows** (+84), and mock writer
  starts advanced **17 -> 19**.
- The unit, marker and Docker image were otherwise unchanged.
  Post-test cleanup restored Docker restart=no, stopped the writer,
  disabled the QA unit and preserved pending.

Full evidence is recorded in Issue #340:
https://github.com/iSentinel/shelly-voltage-telegram/issues/340

This contrast demonstrates the exact real-boot restart-owner bypass
that no purely static systemd inspection can rule out. Merely
adding a pending check in ExecStartPre does not guard a container
Docker can start autonomously. A safe signed release must prove
both outage availability *and* that no writer runs during unresolved
pending-update, with newest whole SQLite/WAL and Telegram PHOTO state
intact. These are **guest-reboot** results, not sudden physical power
loss, NVMe cache durability or an actual production signed rollback.
The current production image, Compose restart policy, unit and
updater timer were not changed.

## Three-phase WAL + owner contention integration QA, 2026-11-19

Two additional cross-process tests now connect the shared OS
writer-lease/pending-update protocol to actual synthetic SQLite
in WAL mode, including three-phase phase labels/frequency and the
existing Telegram PHOTO marker. These tests use unique temporary
directories; nothing reaches live Shelly, Telegram or production.

- With a paused systemd-model writer holding its lock, the QA
  updater marker is introduced between first and second pending
  checks. The writer resumes but returns BLOCKED_PENDING without
  adding any WAL rows; historical L1/L2/L3 and PHOTO state match.
- With a Docker-model writer holding a lock and using WAL, real
  SIGKILL terminates it. A separate updater process acquires the
  released lock and publishes pending-update. A new systemd-model
  writer sees pending and refuses before SQLite writes; the
  committed first-writer L1 record and both other phases plus the
  PHOTO marker remain intact.
- This is intentionally a *cooperating synthetic writer*:
  the production signed bot and its Docker/systemd startup paths
  have NOT been changed to acquire the same lock.

Strict regression results: 11/11 owner concurrency tests including
these 2 WAL cases PASS, 34/34 boot-audit tests PASS, source quality
over 247 Python files PASS. The earlier actual LXC114 A/B reboot
result still proves that an autonomous Docker restart bypasses a
mere systemd ExecStartPre pending check unless the writer itself
participates in the shared fencing design.

Release gate remains BLOCKED: a common production writer contract,
reliable supervisor, exact source/OCI rollback and latest complete
SQLite/WAL history with PHOTO identity must all be accepted together.
