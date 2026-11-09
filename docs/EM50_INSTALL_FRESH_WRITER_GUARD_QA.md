# Reject fresh-target installation while an existing Shelly writer runs

## Reproduced failure

The writer-quiescence guard previously returned early for any root without
an existing VERSION. In a disposable Docker-inspection simulation, a fresh
or alternate install target was accepted (exit 0) even when the existing
named `shelly-voltage-bot` container was running. A later Compose up
could therefore attempt to replace a live monitoring writer.

## Guard

On actual (non-dry-run) installs, inspect the Shelly container if Docker
is available regardless of the selected destination root. A confirmed
running writer or unexpected state blocks installation before Docker
setup or source changes. For an existing versioned installation, Docker
inspection failure remains fail-closed. A fresh installation with no
Docker service or no existing named container remains allowed.

Dry-run never touches Docker and only shows intended changes.

## Isolated acceptance

- RED: fresh install root + simulated running writer returned success.
- GREEN: running writer blocked with exit 5; stopped or absent writer
  allows fresh install; dry-run makes no Docker inspection.
- 7/7 focused writer gate tests PASS.
- Python 3.13: 1319/1319 isolated test suite PASS.
- Ruff 0.14.2 formatting/lint and project quality PASS.
- Four installer dry-run fixtures and shell syntax PASS.

Keep this as a Draft PR stacked on PR #282. No production Docker
stop/restart, database write, Telegram send/edit, Shelly action, merge,
installer deployment or release. Independent human source review,
stopped-writer full-history backup parity, protected YASNO recipient,
real-client Telegram visual acceptance, signed rollout/rollback approval
remain mandatory for release.
