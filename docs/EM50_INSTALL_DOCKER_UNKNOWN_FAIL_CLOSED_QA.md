# Fresh install: distinguish Docker absence from inspection failure

## Reproduced defect

The fresh-target writer guard introduced in PR #283 used to accept any
failed `docker inspect shelly-voltage-bot` as proof that no writer was
running. The same failure status can result from an unreachable Docker
daemon or inability to inspect an existing named container. An isolated
stub demonstrated exit code 0 for both cases.

## Fail-closed preflight

If Docker is installed and a fresh target has no VERSION, a failed
inspection now requires a successful `docker container ls --all`.
The fresh installation is allowed only when that accessible container
list confirms the exact `shelly-voltage-bot` name is absent.

An unavailable daemon, failed listing or still-listed Shelly container
fails with exit status 5 before Docker setup, backup capture or any
source and SQLite mutation. Existing versioned installations continue
to reject inspection failures. Dry-run avoids Docker access.

## Independent QA

- RED: two unsafe fresh target states accepted (unknown daemon and
  listed-but-uninspectable named container).
- GREEN: both cases exit 5; empty named-container list and confirmed
  stopped writer are allowed.
- 8/8 focused writer-gate tests PASS.
- 1320/1320 isolated Python tests PASS (Python 3.13).
- Ruff 0.14.2 and project quality PASS; four installer dry-run
  fixtures and Bash syntax PASS.

**No deployment authorization:** this is a Draft PR on PR #283.
Independent human source review, quiesced exact-history SQLite and
source snapshot, protected YASNO recipient, real Telegram client
visual approval and signed rollout/rollback are still required.
No production Docker restart, database write, backup capture,
Telegram send/edit or Shelly command occurred.
