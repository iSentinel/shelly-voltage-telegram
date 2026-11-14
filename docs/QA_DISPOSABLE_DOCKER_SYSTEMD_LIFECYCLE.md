# Disposable Docker/systemd lifecycle acceptance

**QA-only; not a production Shelly maintenance executor.**

The integration exercise uses the existing Docker engine in LXC 110
to create exactly one temporary, unconnected container and one
transient systemd service. It never stops, updates, restarts, or
mounts the live Shelly container, source, database or Telegram app.

The temporary container uses an already-local image **ID** read
from the running Shelly container, but overrides the entrypoint
with an unprivileged shell running a sleep loop. No image pull/build
occurs. The test container has no network, no ports, no volumes,
no production environment or secrets, a read-only root, all Linux
capabilities dropped, no new privileges, a 64 MiB memory limit and
a 32-process limit.

The transient systemd service runs only /bin/sleep 60, under a
random unique name, NoNewPrivileges, PrivateTmp and MemoryMax=32M.
It is collected after stop, never installed as a persistent service.

## Commands

The default is a dry-run with no shell/systemd/Docker commands:

    python3 scripts/disposable-docker-systemd-lifecycle-qa.py --dry-run

Explicitly run an isolated test:

    python3 scripts/disposable-docker-systemd-lifecycle-qa.py \
        --run-disposable \
        --confirm DISPOSABLE_SVT_DOCKER_SYSTEMD_QA_ONLY

Use -h or --help. Execution requires root, Docker, systemd-run
and systemctl. Without the exact confirmation it refuses to act.
The CLI accepts no container name, systemd unit, image or host-path
override from the caller.

## Verified real lifecycle

First the script inspects the production container **read-only**.
It then creates the isolated test container and:
1. Starts the disposable sleeper under restart=unless-stopped.
2. Changes only its restart policy to no.
3. Stops it and verifies Running=false and restart=no.
4. Injects an unexpected QA restart and confirms the stopped-writer
   fence refuses to regard the running process as stopped.
5. Stops the test container again and fsyncs a private checkpoint.
6. Starts it under the still-neutralized restart policy.
7. Restores its original restart policy.
8. Runs and stops a transient systemd sleep service.
9. Deletes generated QA objects and private checkpoint; verifies
   the live production container ID, running state, restart count
   and image ID are identical to the pre-test values.

Both the first manual exercise and the repeatable script completed
PASS on LXC110, 2026-11-14. The 15 dedicated unit tests and
project-wide Ruff quality check also passed locally.

## Boundaries and mandatory future work

The temporary systemd service and Docker container are exercised
**separately**. The transient unit does not supervise Docker. This
proves isolated control APIs but does NOT prove that all actual
production restart owners (Compose startup, updater timer, boot
recovery, watchdog and Docker restart policy) can be fenced together.
It is not a power-loss durability test.

No production database is backed up or replaced. The real A/B
source+SQLite archive exercise uses separate disposable source and
database fixtures in PR #329. Multi-owner sequencing is tested
with a synthetic state model in PR #331.

After Shelly came online, source preflight reported PREFLIGHT_PASS
but source_install_authorized=false. A previous administrative
source install was denied; this QA does not override that denial,
trigger an upgrade or enable the Telegram TEXT-to-PHOTO cutover.

Remaining gates: independent review of whole-writer restart fences;
fresh fully quiesced source/SQLite A/B backups; reboot/power-loss
recovery rehearsal; explicit owner approval; matching signed image
and source identity; stable Shelly telemetry; independent Telegram
status and pinned-message continuity audit.
