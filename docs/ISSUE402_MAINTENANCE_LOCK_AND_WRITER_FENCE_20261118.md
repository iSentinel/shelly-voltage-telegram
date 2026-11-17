# Issue #402 — updater-lock ordering and single-writer maintenance fence

## Field incident and resolution, November 17, 2026

During the owner-authorized release v0.5.23-hw update, the initial
pre-cutover backup was performed with a stopped Docker writer. Its
SHA256 and full SQLite history, outage and Telegram status parity
checks passed. However, the operator's backup shell retained
the updater lock /run/lock/shelly-voltage-updater.lock while calling
systemctl start shelly-voltage-compose.service. That unit's
ExecStartPre runs apply-update.sh --recover, which needs the **same
lock**. Systemd waited and failed until the original operator
command released the lock. The previous signed image was restored
to ready service before proceeding.

During later official source alignment, systemctl stop made the
Docker container Running=false, but Docker still reported restart
policy unless-stopped. The existing signed-image installer guard
correctly rejected it as WRITER_NOT_STOPPED_AND_FENCED.
The operator safely restored the signed service, then performed a
new controlled stop, set Docker restart=no *for that maintenance
window*, verified the official signed installer guard, and ran the
official source installer. The installer returned PASS; the normal
unless-stopped policy was restored. No guard bypass or second writer.

## Read-only preconditions

The new script scripts/em50-maintenance-fence-audit.py inspects
the signed version/receipt, actual Docker image identity and state,
restart policy, pending-updater marker and availability of the
existing updater lock. It opens that lock read-only and probes it
non-blockingly. It never creates a missing lock file, sends
Telegram messages, writes SQLite, changes any service or authorizes
deployment.

| Phase | Expected Docker | Expected policy | Updater lock |
| --- | --- | --- | --- |
| live | running | unless-stopped | free |
| fenced | stopped | no | free |
| before-start | stopped | no or unless-stopped | free |

Example for an already healthy signed installation:

    python3 scripts/em50-maintenance-fence-audit.py --phase live --jsonl

Do not run the fenced check against a running service and do
not treat a live PASS as authorization for cutover or PHOTO.
A PASS is only an instantaneous diagnostic; another process can
acquire the lock after the check. Retain the release's strict
single-writer installer/updater guards for actual transactions.

## Future exact-source alignment after owner approval

1. Verify immutable signed OCI digest, version/receipt parity,
   /ready, SQLite, Telegram identity, YASNO and selected rollback
   source. Download the **official** release asset and SHA256SUMS;
   run installer --dry-run --non-interactive before any stop.
2. Ensure no other updater is running and retain the source and
   full-history SQLite backup/rollback evidence. Do not hold the
   updater lock while asking systemd to start its own service.
3. In a protected maintenance window, stop the single service with
   systemctl stop shelly-voltage-compose.service, inspect
   docker inspect shelly-voltage-bot, then set
   docker update --restart=no shelly-voltage-bot. Require
   --phase fenced PASS; the original
   installer-signed-image-guard.py must independently PASS.
4. Run the **verified** official release's installer using
   SVT_ROOT=/opt/shelly-voltage bash scripts/install.sh --non-interactive.
   It must snapshot the old sources and entire SQLite history and
   verify parity before copy. It uses the existing pinned signed
   image, not a local Docker build.
5. Confirm deployment/source parity, official digest, systemd,
   /ready, SQLite, Telegram and YASNO are all PASS and the
   normal unless-stopped policy has returned. A --phase live
   check must PASS on the newly installed source.
6. If the installer aborts **before copying anything**, release
   any retained shell lock, restore the old signed container restart
   policy and use systemd to start the safe image. If copying or
   SQLite migration may have begun, **do not blindly start**:
   use the protected rollback snapshot and updater recovery plan.
   A rejected guard is not permission to disable that guard.

## Test evidence and boundaries

Synthetic tests cover held updater locks, stopped/running Docker,
restart=no versus unless-stopped, mismatched version/receipt,
wrong OCI digest, symlinks, missing lock and pending updates. They
run as root and nobody in independent network-disabled, read-only
containers. No real Docker state or production SQLite is modified.
The before-start phase explicitly rejects a lock still held by
a parent process.

The owner has accepted catastrophic one-NVMe loss risk. Backup
files on the same host cannot be called off-host redundancy.
Public Telegram PHOTO remains disabled absent separate opt-in.
