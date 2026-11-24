# Issues 340/422: block old updater while sole-owner fencing is active

Date: 2026-11-19. Status: SOURCE-ONLY QA. NOT production-deployed.

The legacy scripts/apply-update.sh and scripts/rollback.sh can
replace a current SQLite database using an older snapshot. In
particular, apply-update.sh --recover deletes the SQLite WAL/SHM
sidecars before replacing the base database. This would lose newer
committed L1/L2/L3 measurements if a new supervised writer had
already advanced the history.

Both scripts now fail at their entrypoints before any Docker, SQLite,
backup, network or service action when any of these signals exists:
- SVT_SUPERVISOR_OVERLAY is set (protected Compose ownership).
- SVT_WRITER_FENCE_DIR is set (shared transaction and lifetime locks).
- /var/lib/shelly-voltage-fence/.svt-owner.lock exists, indicating
  the protected single-owner fence was provisioned.

The sourced functions in apply-update.sh also refuse execution:
recover_pending, restore_snapshot_database, activate and start_stack.
This closes a bypass around its guarded main. Help remains available
under protected supervisor mode.

A protected single-owner rollout requires a new updater/rollback
transaction. These guards deliberately DO NOT implement a new
updater, rollback, signed release, database restore or recovery.
Do not activate the candidate supervisor while the old updater is
still used as its boot recovery. A future, independently approved
transaction must replace that boot path.

LXC114 isolated QA:
- 7/7 focused legacy guard tests PASS (including sourced functions).
- 34/34 existing update/rollback/YASNO/legacy tests PASS at initial
  validation; later selected update/guard tests 25/25 PASS.
- Full joined selected regression 189/189 PASS in networkless,
  nonroot, read-only QA Docker (only test-fixture /tmp executable).
- Ruff0.14.2 + project maintainability PASS.
- Production LXC110 service, SQLite, timer, Telegram and signed OCI
  unchanged. No Docker restart, no old recovery attempted.

NEXT: guarded source/image pair preflight, immutable newer SQLite WAL
rescue + PHOTO identity, one updater/rollback transaction, interruption
and guest reboot checks, exact-head Python 3.11/3.12/3.13 CI,
then separate Owner Approval before any production change.
