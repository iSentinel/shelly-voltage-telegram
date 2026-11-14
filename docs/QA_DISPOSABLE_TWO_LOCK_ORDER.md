# Disposable two-lock ordering QA

**Issue #340. QA ONLY. Not a production updater or boot policy.**

## Why a separate coordination lock is required

A genuine networkless LXC114 timer regression showed a synthetic
writer restart could occur while a simulated updater held an
exclusive maintenance lock and no pending-update marker existed.
PR #350 adds a shared private maintenance lock around the guarded
systemd start, preventing that limited test-only restart race.

Production apply-update.sh and the boot --recover hook currently
take an existing separate updater mutex at
/run/lock/shelly-voltage-updater.lock. Replacing that mutex with
the maintenance lock may deadlock: a supervisor holds SHARED and
waits for systemd, while its ExecStartPre waits for EXCLUSIVE on
the same inode.

A safe *candidate*, not deployed:
- Updater first takes maintenance EXCLUSIVE, then separate
  updater-mutex EXCLUSIVE, with bounded waits.
- Guarded supervisor takes maintenance SHARED nonblocking,
  holds it throughout a narrowly scoped systemd restart, and
  never directly acquires updater mutex.
- Boot ExecStartPre may acquire its distinct updater mutex,
  but must not reenter the shared maintenance lock EXCLUSIVE.
- Never acquire updater mutex first and later request
  maintenance EXCLUSIVE; this inverted order is a circular-wait
  hazard against a supervisor holding maintenance SHARED.
- Every actual restart owner must comply with the protocol.
  Pending markers, status checks, fresh complete SQLite A/B,
  signed code trust and service availability remain independent
  acceptance gates.

## Process-level isolated proof

The executable creates two new, different 0600 lock inodes
under a random private 0700 /var/tmp directory. It does not
accept paths, read production services or invoke Docker,
systemd, Shellly hardware, Telegram or updater.

By default it performs no work:

    python3 scripts/disposable-two-lock-order-qa.py --dry-run

Explicit private fixture-only process test:

    python3 scripts/disposable-two-lock-order-qa.py \
      --run-disposable \
      --confirm DISPOSABLE_TWO_LOCK_ORDER_ONLY --jsonl

Independent Python processes use nonblocking fcntl.flock to
prove that same-inode EXCLUSIVE prestart conflicts with the
supervisor SHARED lock, while a separate updater mutex works.
Updater EXCLUSIVE maintenance fences the supervisor SHARED
lock. With deliberately reversed ordering, two *separate*
holders take their first lock and then each fails to acquire
the other's lock. A two-barrier handshake ensures both hold
their first lock until both second-lock attempts finish.
Each child has bounded waits, and parent cleanup terminates
leftovers; all scratch files are removed.

The first single-barrier prototype showed a timing race and
was not accepted. The corrected two-barrier implementation
passed three independent repeated process-level tests on
2026-11-15 and dedicated automated unit tests.

## Release gate

Advisory lock behavior is not complete writer fencing:
a process that ignores the lock can still start; a disabled
unit or failed update recovery can remain blocked; incomplete
source and SQLite snapshots can lose data. Real boot recovery
must be checked for lock-order inversions, timeout, stale
owner processes, pending metadata and physical interruption.

No production writer, restart policy, updater or Telegram
status is modified. Production release/source install and
Telegram TEXT-to-PHOTO remain NO_GO until independent
owner-approved maintenance and recovery acceptance.
