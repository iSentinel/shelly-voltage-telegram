# Issue340/422/431: QA-only fenced update preparation

Date: 2026-11-19. Status: SOURCE-ONLY / QA PASS / PRODUCTION BLOCKED.

This increment is a *preparation and recovery-inspection* fixture for
an isolated container. It does not change, activate, sign, publish or
roll back a production image. It cannot be used against LXC110.

## Safety constraints

The source-only scripts/stage-guarded-update.py requires:
- Explicit --qa-only; container name starting svt-qa-.
- An SQLite DB located directly within /var/tmp/svt-qa-*.
- A root-owned private rescue directory under /var/lib/svt-qa-*.
- A root-controlled shared lock directory under /var/lib/svt-qa-*.
- An existing Docker container with restart:no, network:none,
  readonly root filesystem, user=10001:10001 and *exactly*
  three expected bind mounts: application /src:ro, data /data:rw,
  protected fence /run/svt-fence:rw.
- The real storage fence marker / owner lock shared with Docker.
- No pre-existing marker and no previous rescue output on first stage.

The operation does **not** read Shelly, use Telegram tokens, contact
GitHub or activate any new runtime. All network devices are disabled.

## What stage does

1. --dry-run validates paths, Docker ownership, pending state and
   output permissions, but writes nothing and stops no process.
2. Stage publishes a durable root-owned pending marker under the
   same inode as the real application's Storage transaction fence.
3. It stops only the selected synthetic container, confirms Docker
   says it is stopped, and demands an independently released lifetime
   owner lease before creating the SQLite backup.
4. The existing fenced-newest-rescue.py copies committed SQLite/WAL
   using the backup API. It checks L1/L2/L3, saved pro3em, PHOTO,
   quick_check and full logical SQL parity. Publication uses a
   no-clobber hard-link operation rather than os.replace.
5. A root-private, non-overwriting QA receipt stores the backup SHA256,
   token and *explicit false* release/rollback authorization flags.
6. Pending remains set and the stopped writer is NOT restarted.

## Inspection and recovery

- --inspect is read-only and requires exact marker token, a stopped
  writer, available lifetime lease, valid root-owned rescue and
  receipt files, matching SHA256 and full database logical parity.
- --recover-receipt can create ONLY a missing QA receipt while pending
  remains set and the writer is stopped. It requires complete logical
  SQLite parity, pro3em model and PHOTO identity. It never restores
  SQLite into the active database or starts Docker.
- A duplicate receipt, altered hash, modified L3 voltage/history,
  stale marker or changed owner state prevents recovery.
- If a stage fails *after publishing pending*, it leaves the marker
  for deliberate owner investigation. No automatic clear or rollback.

## Real isolated LXC114 acceptance

The actual disposable Docker writer and root updater process passed:
- Docker independent restart policy => rejected.
- Production SQLite path => rejected.
- Dry-run => no state changes; writer remained running.
- Stage => pending published, writer stopped, latest rescue created.
- SQL logical digest source/backup equal, all L1/L2/L3 and
  Telegram PHOTO identity intact.
- SHA256 QA receipt and nonauthorizing flags verified.
- Re-entry while pending => refused.
- Inspect of complete staged transaction => PASS.
- Missing receipt => inspect refused; backup and marker preserved.
- Altered L3 voltage in rescue => recover-receipt refused.
- Original validated rescue restored in QA => recovery PASS.
- Forged SHA256 in receipt => inspect refused.
- Duplicate receipt creation => refused.
- All temporary QA Docker containers and markers cleaned.

An attempted process-level termination test was NOT completed; it
must NOT be reported as a real SIGKILL PASS. Missing-receipt fault
injection confirms fail-closed *state* handling, not kill-boundary
durability. Actual SIGKILL after rescue, before receipt, remains open.

The combined 147 selected source unit tests on Python 3.13 passed in
a networkless, secretless, nonroot Docker QA runner. Ruff 0.14.2 and
project maintainability checks passed. This is not exact-head CI
for Python 3.11, 3.12 and 3.13.

Production read-only check while mains/Shelly unavailable:
- LXC110 signed v0.5.25-hw kept running with Docker health healthy,
  readiness HTTP503, no phantom 0 V measurements.
- Three open per-phase outages, last readings at 06:07:24 Kyiv.
- Production github_update.py preflight refused with HTTP 503.
- No live service restart, install, new image, DB rewrite or message.

## Next required release stage

A true signed updater/rollback transaction must integrate the above
source-only fences, a single systemd restart owner, exact signed
OCI/source pair, most-recent full-history SQLite/WAL+PHOTO rescue,
crash-point SIGKILL/reboot acceptance, positive recovery and independent
Owner Approval. Until that contract is complete, never deploy the
partial QA staging tool as a production workaround.

## QA CLI examples

Dry run (strictly offline synthetic container):
python3 scripts/stage-guarded-update.py --qa-only \
  --container svt-qa-example --database /var/tmp/svt-qa-example/history.sqlite3 \
  --fence-dir /var/lib/svt-qa-example-fence \
  --output /var/lib/svt-qa-example-rescue/newest.sqlite3 \
  --token qa-example-1 --dry-run

After explicit setup of matching private QA assets, replace
--dry-run with --inspect, --recover-receipt or no mode to run the
requested operation. None of these options grants OTA permission.
