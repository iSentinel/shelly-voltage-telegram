# Disposable external restart-owner timer acceptance

**QA-only. Do not use for live Shelly maintenance.**

This independent regression proves why stopping a Docker writer is not
enough for an exact source/SQLite A/B snapshot if an enabled external
systemd timer can start the writer again.

It creates only a uniquely named, networkless, non-root temporary
Docker sleeper with no volumes, published ports, credentials, or
access to production paths. The Docker image ID comes from a read-only
production inspect, but the app entrypoint is replaced by a sleep
loop. Read-only root, all capabilities dropped, no new privileges,
64 MiB memory and 32 PID limits are enforced.

Two disposable, one-shot systemd timer/service pairs are generated:
- Unfenced: Docker writer is initially stopped; the timer issues
  Docker start only for that disposable name. The QA must observe
  the test container running.
- Fenced: Docker writer is stopped again; the new timer is explicitly
  stopped before firing, then the QA verifies the container stays
  stopped past its scheduled activation.

The transient timers use AccuracySec=1ms and RandomizedDelaySec=0.
Without the explicit AccuracySec, systemd may delay a monotonic timer
within a one-minute coalescing window and a short QA observation can
produce a misleading failure.

## Commands

Safe default:

    python3 scripts/disposable-timer-restart-fence-qa.py --dry-run

Explicit disposable integration on a test-capable host:

    python3 scripts/disposable-timer-restart-fence-qa.py \
        --run-disposable \
        --confirm DISPOSABLE_SYSTEMD_TIMER_FENCE_QA_ONLY

Use -h or --help for arguments. There are no caller-supplied
container names, timer names, Docker images, paths, or commands.

The script checks production container ID, image, running state, and
restart count before and after and fails closed on any change. It
stops and resets both generated transient timer/service pairs,
removes the QA container and verifies no QA objects remain.
The QA may not stop, disable or otherwise modify any production
timer, service or container.

## Evidence

November 14, 2026, isolated LXC110 test:

- Unfenced test timer restarted the previously stopped QA sleeper:
  PASS.
- Explicitly stopped one-shot test timer did not restart its writer:
  PASS.
- No test Docker container or transient systemd unit left behind.
- Production Shelly Docker Healthy, zero restarts, /ready=200.

19/19 dedicated mock unit tests and full project Ruff quality PASS.
GitHub CI Python 3.11/3.12/3.13 must also pass before PR review.

## Remaining limits

This proves the timer restart *mechanism*, not that all real startup
owners have been fenced at once. Actual production includes an active
and enabled Compose unit with boot ExecStartPre --recover, a running
Docker writer with unless-stopped restart policy, an enabled updater
timer, and potentially other restart mechanisms. The real writer
must remain untouched until all independent owners have verified
quiescence and exact current-history backups are available.

Separately, Issue #335 and PR #336 address the historical SQLite
rollback hazard. Their QA tests do not authorize live installation.
The running signed image v0.5.21-hw still differs from host source
v0.5.15-hw; production source installation was administratively
denied and requires new explicit owner approval. Telegram TEXT->PHOTO,
pin/message continuity and power-loss acceptance remain NO_GO.
