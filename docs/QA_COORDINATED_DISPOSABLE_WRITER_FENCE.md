# Coordinated disposable writer-owner fence and A/B acceptance

**QA-only integration. Not a production maintenance executor.**

This regression combines three independent restart mechanisms for one
**synthetic, temporary SQLite writer**:

1. A transient systemd supervisor running Docker start --attach,
   with its own ExecStop.
2. A second transient one-shot systemd timer that can issue Docker
   start independently of the first supervisor.
3. The disposable Docker container's restart=unless-stopped policy.

The writer uses a private /var/tmp SQLite fixture initialized with
120 synthetic samples. Its only mount is that QA fixture under /qa.
The QA Docker container has no network, ports or credentials, a
read-only root filesystem, dropped capabilities, no-new-privileges
and resource limits. There are no paths to production databases,
environment files, release signing keys, or Telegram.

The script uses an already-local immutable image ID from read-only
inspection of the production container, but completely overrides
the entrypoint with a harmless synthetic Python/SQLite writer.

## Commands

Default mode does not invoke Docker or systemd:

    python3 scripts/disposable-coordinated-writer-fence-qa.py --dry-run

Real isolated QA requires root and exact opt-in:

    python3 scripts/disposable-coordinated-writer-fence-qa.py \
        --run-disposable \
        --confirm DISPOSABLE_COORDINATED_WRITER_FENCE_QA_ONLY

Use -h or --help for usage. No user-selected image, unit, container,
mount, source directory, database or production command is allowed.

## Phases

- Start test writer from the transient supervisor, prove SQLite rows
  advance.
- Stop only this QA container, **wait until supervisor ExecStop has
  fully completed** and then arm the independent test timer with
  AccuracySec=1ms, RandomizedDelaySec=0.
- Prove the timer independently restarts the test writer and new
  SQLite samples arrive.
- Fence *all three modeled owners* in order: stop timer and its
  transient service, stop supervisor, set QA Docker restart=no,
  stop QA Docker container.
- Require both transient owners inactive, Docker stopped/policy=no,
  and unchanged SQLite history across a stability observation.
- Capture A/B source archives and independent whole SQLite backups
  using the SQLite backup API. Validate both copies against the
  full logical SQLite history, user_version and application_id;
  validate the synthetic source marker within each archive.
- Resume only QA writer from a controlled start and prove SQLite
  sample count advances, then stop it and clean all QA resources.
- Verify the actual production Docker container identity and
  real Compose/updater timer active/enable states did not change.

## Evidence: LXC110, November 14, 2026

- Initial synthetic database: 120 samples.
- Unfenced external timer restarted synthetic writer: 141 rows.
- Coordinated Docker/systemd/timer writer fence: PASS.
- Two independent exact A/B SQLite+synthetic-source archives:
  **161 rows each**, whole-history parity PASS.
- Controlled resumption after A/B: 162 rows, PASS.
- All temporary Docker containers and transient systemd units
  removed; production Healthy, zero restarts, /ready=200.
- **31/31 local focused tests** with ResourceWarning as error;
  project Ruff quality gate PASS.

A first integration attempt detected a supervisor ExecStop versus
timer-start race; waiting for the supervisor to become inactive
corrected that test ordering. A second full integration completed
successfully. These are both useful regression findings.

## What this does NOT prove

This test demonstrates the interaction of **three modeled** writer
restart owners. It does *not* fence, stop or disable the actual
production boot ExecStartPre --recover, enabled Compose autostart,
enabled updater timer, any scheduled administrator action, or any
other production restart mechanism. Those real conditions still
fail the read-only owner audit.

SIGKILL process testing is not actual power-loss durability, and
the synthetic source marker is not a signed official release.
Neither this regression nor successful GitHub CI authorizes
production source installation, stopping a live writer, restoring
live SQLite, changing Telegram message/pin identity, or switching
the Telegram status from TEXT to PHOTO.

Signed production runtime remains v0.5.21-hw, host source
v0.5.15-hw. Owner approval and an independently verified
writer-fenced exact fresh source+whole-current SQLite A/B snapshot,
signed parity, real restart-owner exclusion, power-loss recovery
and channel continuity checks must precede any live maintenance.

Related: PR #333 isolated SQLite writer lifecycle, PR #337 external
timer only, PR #336 candidate pending-update history preservation,
and Issue #335 historical rollback hazard. None is deployed.
