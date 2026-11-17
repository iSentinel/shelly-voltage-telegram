# Issue #402 — deterministic updater readiness test fixture

## Field and QA context

Production runs one healthy Shelly voltage Telegram writer in LXC110.
The on-host `shelly-voltage-compose.service` uses the ordered sequence
`apply-update.sh --recover` followed by `start-compose.sh`. Neither
script nor the running container was executed, restarted or modified
during this QA work.

The source-only `test_update_ready_wait.py` originally emulated
`curl` with a new `/usr/bin/env python3` process on each invocation.
On 2026-11-17, the synthetic 60-poll refusal cases exceeded the
test's 10-second subprocess timeout even without Docker CPU throttling.
The observed failure was `subprocess.TimeoutExpired`, not evidence
that the actual updater ignored readiness or attempted an unsafe boot.

## Minimal QA-only change

Replace only the disposable fake-`curl` executable in
`tests/test_update_ready_wait.py` with a POSIX shell fixture. It
accepts the same `READY_CODES` sequence and `READY_COUNT_FILE`
counter, validates that the requested route ends in `/ready`, returns
the last configured status after the finite sequence, and uses only
shell builtins. No production `scripts/apply-update.sh` change.

Keep the four intended properties:

- HTTP 503 before three ready 200 responses must not permit an early
  update success.
- A transient 503 resets consecutive ready successes.
- Permanently unavailable readiness fails after 60 synthetic probes.
- Oscillating 200/503 also fails after 60 probes.

The shell fixture removes Python interpreter startup costs from
each poll, so the 10-second test timeout remains meaningful.

## Acceptance and scope

The focused `test_update_ready_wait.py` suite passes all 4/4
tests as root and UID nobody with a read-only, network-disabled,
capability-free QA container.

The wider synthetic boot/writer batch passes 52/52 per UID
(**104/104 total**):

- `test_install_writer_gate.py`: 8
- `test_install_existing_database_guard.py`: 4
- `test_install_snapshot_order.py`: 2
- `test_update_ready_wait.py`: 4
- `test_start_compose_yasno.py`: 14
- `test_source_reconcile_recovery_preflight.py`: 11
- `test_install_snapshot_retention.py`: 3
- `test_update_interruption.py`: 6

This is **synthetic acceptance**, not a real power-cycle, actual
systemd recovery/reboot rehearsal, signed release, owner risk waiver
or Telegram channel acceptance. No active OTA, Docker deployment,
SQLite restore or public Telegram send is authorized by this test.

There is no second independent device in the project. Keep
`offhost_backup_verified=false`, `release_authorized=false` and
`photo_authorized=false` until separate decisions and technical
release reviews are complete.
