# Disposable systemd-supervised SQLite writer regression

**Independent QA-only lifecycle regression. NOT a production maintenance
executor or authorization to stop the running Shelly bot.**

The tool runs real Docker and transient systemd operations only on
newly generated, uniquely named disposable objects. The already-local
production Docker **image ID** is read, without executing its app:
the QA container runs a completely independent Python loop that inserts
synthetic voltage samples into a synthetic SQLite fixture.

The tool creates a new owner-private scratch directory under
/var/tmp; that is the **only** mounted path into its test container.
The QA container has no network, ports or production credentials.
Its root filesystem is read-only, capabilities are dropped, no new
privileges are allowed, memory and process counts are limited.
User 0:0 is confined to this networkless, capability-free QA container
so it can write into the owner-private scratch mount. Production
SQLite at /var/lib/shelly-voltage is never referenced by this script.

## Usage

Default mode does not execute any external commands:

    python3 scripts/disposable-supervised-writer-regression-qa.py --dry-run

Run a disposable integration on a QA-capable host that already has
the Shelly container and Docker/systemd:

    python3 scripts/disposable-supervised-writer-regression-qa.py \
        --run-disposable \
        --confirm DISPOSABLE_SUPERVISED_SQLITE_WRITER_QA_ONLY

Use -h or --help for options. There are no user-supplied container
names, systemd units, shell commands, source paths, image tags,
database paths or mount overrides. Root is required for real QA.

## Real integration sequence

1. Inspect the production container ID, restart count, running state
   and image ID, read-only.
2. Create synthetic SQLite with 120 rows and synthetic source README,
   plus a random test Docker writer with restart=unless-stopped.
3. Launch a transient systemd service that calls Docker start --attach
   for only that test writer, with an ExecStop of Docker stop for only
   that test writer. Confirm fresh synthetic samples are added.
4. Set only the test container restart policy to no, stop its
   transient service, confirm it is no longer running and samples
   stop changing.
5. Capture distinct SQLite A/B backup API copies and synthetic-source
   A/B tar.gz archives. Restore each SQLite archive, validate quick
   integrity, full logical history and PRAGMA user/application IDs.
   Also validate the source archive's member names **and contents**.
6. Re-launch the synthetic writer under a new transient systemd unit,
   confirm new samples, then disable only its Docker auto-restart.
7. Send SIGKILL only to the test writer; verify stable SQLite
   contents, and make and restore a second independent A/B pair.
8. Resume the same test writer under a final transient systemd
   service while its restart policy remains no; verify new samples
   and restore its original restart policy afterward.
9. Stop, reset and remove only the generated QA units/container and
   private scratch fixture, verify no exact QA objects remain, and
   assert the production container's identity has not changed.

SQLite archive decompression is bounded to 16 MiB for this fixture.
This cap is **only for synthetic QA**, not the production database.
Failures of external commands or corruption of either archive fail
closed. No historical production data is restored.

## Evidence, validation and limitations

On LXC110, 2026-11-14, full QA-only integration PASS:
- first test archive pair: 142 synthetic measurements each;
- after a real SIGKILL of the disposable writer: second archive pair
  restored 144 measurements each;
- controlled systemd-supervised resume advanced to 145 samples;
- all QA Docker and transient systemd objects cleaned;
- production container Healthy, restart count zero, /ready HTTP 200.
- 25/25 targeted unit tests, ResourceWarnings as errors, and full
  Ruff project quality gate PASS.

These results demonstrate process-level stopping, SQLite WAL recovery,
test archive consistency, controlled process resumption and safe
cleanup on this host. They do **not** establish host power-loss
durability, actual production systemd/Compose/updater/restart-owner
fencing, or production data backup safety.

PR #329 independently tests QA source and complete SQLite A/B
archives using an official staged release, while this new regression
intentionally uses tiny synthetic source and SQLite fixtures. The
full signed v0.5.21-hw production release remains running, whereas
installed host source v0.5.15-hw still differs from the OCI image.
A prior administrative production source install was denied; its
preflight returning PASS with source_install_authorized=false is NOT
approval to install, rebuild, restore production data or switch the
Telegram message from TEXT to PHOTO.

NEXT: an independently reviewed all-restart-owner production fence,
fresh signed source and complete stopped-writer SQLite A/B backups,
a safe power-failure recovery plan, explicit owner approval and
Telegram message/pin continuity acceptance.
