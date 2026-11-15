# Issue #340 — read-only production writer-owner inventory

Evidence captured over Gateway SSH on **2026-11-15 11:34–11:40 Kyiv**,
Proxmox LXC110. No production writes or restarts. This file is
**review-only**, not release authorization.

## Actual observed owners

| Actor | Observed installed behavior | B/A owner status |
| --- | --- | --- |
| Docker daemon | Running v0.5.21-hw immutable OCI, restart policy `unless-stopped` | Autonomous restart bypasses advisory B; actual isolated LXC114 bypass reproduced |
| `shelly-voltage-compose.service` | Enabled, Type=oneshot, RemainAfterExit=yes, Restart=no | Not a lifetime lock owner |
| Compose ExecStartPre | `/bin/bash /opt/shelly-voltage/scripts/apply-update.sh --recover` | Uses updater A only; old implementation predates PR #336 |
| Compose ExecStart | `/bin/bash /opt/shelly-voltage/scripts/start-compose.sh` | Calls `docker compose up -d` without B SH lifetime lease |
| Compose ExecReload | Same `start-compose.sh` | Independent direct restart/update path, no B lifetime |
| Compose ExecStop | Direct `docker compose stop` | No comprehensive owner handoff |
| `scripts/start-compose.sh` | Checks Compose config and uses `compose_cmd up -d`, `up -d --no-build` or `up -d --build` | Does not check maintenance B or persistent writer owner |
| `scripts/apply-update.sh` | A lock at `/run/lock/shelly-voltage-updater.lock`; `flock -n` for apply, `flock -w 120` for recover | No independent maintenance B or all-owner fencing |
| `scripts/install.sh` | Invokes `start-compose.sh --build`, then `systemctl start shelly-voltage-compose.service` | Can use more than one start entry point |
| `scripts/rollback.sh` | `docker compose stop`, writer-state check, restores source and DB, then `compose_cmd up -d --build` | Does not have all-owner lock protection; old DB restore risk |
| `shelly-graphic-emergency-boot.service` | Disabled, Restart=on-failure, separate Python restore executor | Conditional/manual path must be reviewed before any enablement |
| Timers | Backup, update-check and YASNO refresh enabled | Inventory their action graph; do not assume they start writer without inspecting |

**Installed source** advertises v0.5.15-hw whereas running
immutable signed Docker runtime is v0.5.21-hw.
As of this audit the running Docker restart count is zero,
readiness is HTTP 200, SQLite quick_check=ok, there is no pending
update marker and private/public version metadata match.
This is a *latent maintenance hazard*, not a current incident.

## Additional real isolated QA findings

- With B EX continuously held, a disposable Docker
  `--restart=unless-stopped` worker was independently restarted
  twice (three inert launches). With Docker `restart=no`, one
  start and zero automatic restarts occurred.
- A real transient systemd startup guarded by B SH rejects a
  Docker start while another process holds B EX, and permits
  one startup after release.
- **Critical lifetime gap:** wrapping only
  `flock B SH docker run -d` permits B EX to be obtained while
  that same Docker container is still running. A *separate*
  `flock B SH docker wait` blocks EX after the lease attaches,
  but contains an **unprotected handoff gap**. It is NOT a fix.
- Actual end-to-end foreground Docker lifetime ownership under
  systemd, global boot/reload coordination and hardware power-loss
  have **not** been proven. LXC114 and LXC110 share one physical
  host; distinct LXC containers are not hardware fault isolation.

QA and negative tests are in draft PR #360. Signature provenance
for its newly created containers was not independently verified
using cosign in this stage; the fixed local image ID was checked.

## Required release-gate architecture review

An independently reviewed solution must prove all conditions:

1. **One authoritative start permission.** Remove uncontrolled
   daemon auto-restart during approved maintenance. Every
   Docker Compose, ExecStart/Reload, manual updater, installer,
   rollback, emergency restore and applicable timer path must
   consult a root-owned gate, not merely rely on voluntary
   locking within one caller.
2. **Writer lifetime protection.** Acquire B SH **before**
   starting the writer, and retain ownership until the actual
   writer exits and its SQLite WAL has been safely quiesced.
   A detached Docker command completing is not a proof of
   stopped writer. Guard process failure, daemon reboot,
   SIGKILL and owner process loss.
3. **Noncircular maintenance.** Maintenance obtains admission
   control, stops and awaits the *actual* current writer,
   verifies it is stopped, obtains B EX then updater A EX
   in a documented order, and avoids any nested boot
   prestart that tries to reacquire A while held by its parent.
4. **Atomic handoff across owners.** A simple B EX release
   followed by unrelated B SH acquire contains a gap.
   Review a durable start-permit/epoch and one designated
   supervisor that prevents any competing writer during
   this handoff, including daemon/host reboot.
5. **Trusted current-state recovery.** Verify exact immutable
   signed OCI digest and source archive, preserve current
   complete SQLite history (including WAL/SHM concurrency),
   create two independently restorable CURRENT backups
   with checksums and a tested restore path. Refuse
   old-snapshot rollback if current history differs.
6. **Durable metadata journal.** Do not infer completed
   update from absent `pending-update`. Reconcile
   private/public version and signed actual running image
   after interrupted publication; verify fsync boundaries
   and crash consistency independently.
7. **Availability safety.** A fail-closed prestart may
   prevent an otherwise healthy monitoring writer from
   starting. Test scheduled reboot, daemon crash and
   unexpected maintenance abort against a defined
   recovery state machine.
8. **Release consent.** Physical power-loss acceptance on
   sacrificial hardware, independent reviewer sign-off,
   explicit Owner Approval, and separately approved
   Telegram pinned TEXT-to-PHOTO acceptance are required.

## Unmerged candidates and residuals

- PR #336 proposes fail-closed stale-history recovery but
  does not grant production authorization.
- Draft PR #358 addresses a false `UPDATE PASS` after
  failed public version sync; post-pending-unlink durability
  still requires the journal.
- Draft PR #359 tests synthetic journal/SIGKILL and real
  transient systemd prestart with mocked signature trust.
- **Draft PR #360** contains actual isolated Docker/
  systemd owner bypass and lifetime-gap QA plus unit tests.

**Production decision: NO-GO.** Nothing in this source-only
inventory authorizes altering Docker restart policy, systemd
unit files, updater, current database or Telegram pinned message.
