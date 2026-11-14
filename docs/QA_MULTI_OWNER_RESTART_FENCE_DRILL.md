# Disposable multi-owner restart-fence drill

**QA simulation, never a production maintenance executor.**

This tool models the approved maintenance sequencing for multiple
independent restart owners: updater timer, Compose autostart, Docker
restart policy, active writer, boot-recovery pending state and exact
source/image identity.

It changes **only JSON files** under a private marked scratch directory
within /tmp or /var/tmp. It never invokes docker, systemctl, Shelly,
the live SQLite database or Telegram.

## Fixture and commands

An owner-only 0700 scratch directory must contain:
- .restart-fence-drill-qa with literal
  DISPOSABLE_MULTI_OWNER_RESTART_FENCE_QA and final newline
- .restart-fence.lock: private unlinked 0600 regular file
- writer-state.json: private unlinked 0600 regular JSON file

The JSON must contain supported boolean owner and snapshot flags,
a supported restart_policy, matching source_version and image_version,
and a synthetic 64-character lowercase hex sqlite_digest.

For an explicitly prepared private disposable fixture:

    python3 scripts/multiowner-restart-fence-drill-qa.py --root /var/tmp/qa-fixture --dry-run
    python3 scripts/multiowner-restart-fence-drill-qa.py --root /var/tmp/qa-fixture --simulate-disposable
    python3 scripts/multiowner-restart-fence-drill-qa.py --root /var/tmp/qa-fixture --simulate-disposable --fail-after capture_a
    python3 scripts/multiowner-restart-fence-drill-qa.py --root /var/tmp/qa-fixture --recover-disposable

All switches have -h/--help. A mismatched source/image or pending
update fails closed before any transition.

## Modeled sequence

1. Quiesce the simulated updater, disable Compose autostart.
2. Neutralize the synthetic Docker restart policy to no.
3. Mark the modeled writer stopped.
4. Before both A and B checkpoints, verify the writer, updater and
   restart owners remain fenced, and the SQLite history fingerprint
   remains unchanged.
5. Resume the modeled writer while restart policy is still no.
6. Restore the original Docker restart policy, Compose startup
   and updater timer, in that order.
7. Only then remove the transaction journal.

The private journal is written before state transitions and synced
after each step. --fail-after models interruptions and
--inject-restart-after models a surprise writer restart. A separate
--recover-disposable command restores only synthetic owner flags.
Recovery refuses changed SQLite digest or a new pending update, and
cannot overwrite or restore any real production database.

## Evidence and limits

24 unit tests PASS, including a failed child process plus separate
recovery, surprise restart before/after A/B, source/image mismatch,
pending update, corrupt journal, linked paths, concurrent peer lock
and safe restart policy order. Full Ruff quality gate passes.

This is NOT an actual Docker/systemd integration test. Its A/B flags
are NOT SQLite/source archives. Independent PR #329 tests the
disposable real source/SQLite archive pair. PR #330 checks actual
production restart owners read-only. The next independent gate is
a disposable production-like lifecycle integration test.

After Shelly came back online on 2026-11-14, the signed source
preflight returned PREFLIGHT_PASS but source_install_authorized=false.
The restart audit still returned BLOCKED. An earlier production
source installation had been administratively denied; this QA test
does not override that, allow old SQLite restoration, or authorize
a production update or Telegram PHOTO cutover.
