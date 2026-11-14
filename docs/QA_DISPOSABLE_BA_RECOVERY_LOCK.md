# Candidate Bash pending recovery under distinct B/A locks

**QA-only, issue #340. No production mutation or rollout authority.**

## Context

Production still runs signed OCI image 0.5.21-hw while installed
host scripts are 0.5.15-hw. The running Docker and Compose policies
are both unless-stopped, and production boot recovery and the
manually invoked signed updater share their existing mutex A:

    /run/lock/shelly-voltage-updater.lock

A separate writer-start maintenance coordination lock B is needed
for a safe proposed single-owner architecture. Reusing the same
inode for B and A can deadlock when a shared-lock supervisor
waits for systemd ExecStartPre to take exclusive A. Independently
PR #351 demonstrated that reversing the acquisition order of
two separate locks can also cause a circular wait.

**Candidate review-only order**: updater/recovery that controls a
writer should acquire B EX before A EX; the guarded supervisor
should hold B SH throughout its protected systemd restart. Boot
prestart may take A, but must never ask for B EX while running as
a child of a supervisor already holding B SH. Every lock wait must
be bounded, and unexpected restart owners fenced separately.

## What this executable actually proves

The test runs only on the known unprivileged, networkless,
loopback-only Debian 13 LXC114 and only when its synthetic Storage
writer is stopped with Docker restart=no. The local QA candidate
scripts are pinned to the expected SHA-256, with the exact
PR #336 apply-update.sh and pending-recovery-history-guard.py,
and it checks the guest name, root UID, network namespace and
QA Docker policy.

Each case creates a private 0700 /var/tmp fixture with a complete
synthetic SQLite backup from the **QA-only** Storage database, a
gzip archive and SHA-256 checksum, recovery metadata and a private
pending-update marker. It copies source only inside this fixture.

The parent holds a private coordination lock B EX on one inode.
The candidate Bash recover_pending() runs in a child with
a **different** private updater mutex A EX. The sole Docker Compose
stop action is intercepted by an argument-validated shell
function that creates a barrier. During this stop interval,
another process verifies B SH and A EX are both blocked.

On release of the barrier, two cases are tested:

1. **Equal history:** candidate recovery's three guard checks
   pass, preserving all SQLite history, restoring the synthetic
   previous metadata and removing the private pending marker.
2. **New write during stop:** the exact already verified signed
   v0.5.21-hw OCI image is launched once as a nonroot, read-only,
   network=none, no-capabilities, no-new-privileges, memory- and
   PID-limited synthetic app.Storage process, with only the
   private QA data directory mounted. It appends 221.7 V.
   The second history guard blocks recovery with exit 1;
   the new sample and pending marker remain intact, no old
   database replacement occurs.

In both cases B SH and A EX remain refused while the candidate
recovery holds both locks, then are available again when done.
The private scratch directory is automatically removed. The
LXC114 standard QA writer, Compose and timer are never started,
and no real Telegram API or production paths are accessed.

First acceptance on 2026-11-15: starting with **876 entirely
synthetic QA measurements**, the equal-history scenario kept
876/876 and removed the fake pending; the injected new write
scenario kept 877/877 and retained the fake pending, with
SQLite quick_check=ok. Both private lock owners were released.

## Repeatable commands

Inert default:

    python3 scripts/disposable-ba-recovery-lock-qa.py --dry-run

Explicit **isolated LXC114 only** invocation, after staging
the exact candidate scripts under /root/svt-pr336-guard:

    python3 scripts/disposable-ba-recovery-lock-qa.py \
      --run-disposable \
      --confirm ISOLATED_LXC114_BA_RECOVERY_ONLY \
      --jsonl

Use -h/--help. Exit codes: 0 for default inert or successful
QA test, 2 for missing exact confirmation, 3 for blocked/failed
QA execution. All successful reports state
production_modified=false, owner_maintenance_authorized=false,
real_docker_writer_restarted=false, and telegram_contacted=false.

The test provides no installer and no production lock policy.
It never creates a production service, scheduled watchdog,
source deployment, SQLite restore or Telegram pinned PHOTO.

## Limits and required follow-up

This is not a fully unmodified production updater transaction:
we source exact candidate recovery Bash under isolated path
overrides, intercept only its Compose stop operation, and use
private B/A locks around the candidate recover_pending function.
The unmodified live apply-update.sh --version entry point
still lacks this maintenance lock protocol, and its source
remains old. An independent end-to-end reproduction involving
all real startup owners, A/B source backup and physical restart
is still required. A process that ignores advisory locks can
still start the writer; metadata commit is not yet globally
atomic if a rogue writer appears after metadata rollback.

Production policy/source install and Telegram TEXT-to-PHOTO
remain **NO_GO** without explicit owner approval, fresh full
current-history source+SQLite A/B and independently tested
rollback/boot continuity.
