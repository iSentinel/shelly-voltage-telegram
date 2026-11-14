# Disposable systemd pre-start hook writer-restart QA

**QA-only. No production startup, reboot, source install or restore.**

This standalone integration tests the mechanism behind the production
Compose service's boot ExecStartPre, without ever invoking the real
apply-update.sh --recover or changing that service.

## Disposable setup

The harness makes an independently named, networkless Docker sleeper
from the production container's already-local immutable image ID.
It completely replaces the image entrypoint with a sleep loop, with
restart policy no, no mounts or ports, read-only filesystem, dropped
capabilities, no new privileges, non-root user and resource limits.

The harness creates two temporary transient systemd services:

- Positive: a one-shot service with an ExecStartPre that issues
  Docker start for only the QA container. The QA must observe that
  the originally stopped container becomes Running with restart=no.
- Negative control: a matching one-shot service without any
  pre-start Docker operation. After stopping QA Docker again, this
  service must not bring the container back.

The program reads actual production Docker ID, image, running state,
restart count, and production Compose/updater unit states before
and after. Any change fails the isolated QA acceptance.

## Usage

Safe default; no host operations:

    python3 scripts/disposable-boot-prestart-hook-qa.py --dry-run

Run an isolated integration on a host with Docker and systemd:

    python3 scripts/disposable-boot-prestart-hook-qa.py \
        --run-disposable \
        --confirm DISPOSABLE_BOOT_PRESTART_HOOK_QA_ONLY

Use -h or --help for help. The user cannot supply a container,
unit, image, path, mount, network target or arbitrary command.
Explicit confirmation is mandatory for real QA.

The finalizer stops and resets only the generated transient units,
removes only its random QA Docker container and checks for orphans.
No user credentials, Telegram API keys, SQLite production records,
or actual power-outage controls are involved.

## LXC110 evidence, 2026-11-14

- Stopped isolated Docker container was started by its transient
  one-shot ExecStartPre: PASS.
- The same container did not start under a control service with
  no ExecStartPre: PASS.
- All QA systemd and Docker resources removed.
- Actual production Shelly Docker Healthy, zero restarts,
  HTTP /ready=200, owner service/timer states unchanged.
- 21/21 targeted mocked unit tests PASS, full Ruff quality PASS.

## Production limitations

This is a **pre-start hook invocation simulation**, not an actual
reboot, unattended recovery or host power-loss durability test.
It does not model all possible production restart mechanisms and
does not disable the installed hook.

Read-only production owner-fence audit remains BLOCKED because
actual Compose is active/enabled, actual Docker has restart policy
unless-stopped, actual updater timer is enabled, and real boot
ExecStartPre needs independent review. QA PR #338 proved coordinated
fencing of three *modeled* restart owners and exact synthetic A/B,
but not fencing the real services.

Signed runtime is still v0.5.21-hw while the host source remains
v0.5.15-hw. Issue #335 and PR #336 contain the QA-only candidate
to prevent a historical SQLite rollback, but are not installed.
A previous administrative production source install was denied.
Owner approval and fresh whole-current SQLite plus signed source
A/B, writer-fenced snapshots, true boot/power-loss acceptance, and
Telegram channel/pin continuity are required before any live
installation or transition from TEXT to PHOTO.
