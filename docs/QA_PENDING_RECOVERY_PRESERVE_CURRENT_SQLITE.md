# QA candidate: preserve current SQLite during pending-update recovery

**Staged source-code candidate only. Not approved or installed on LXC110.**

## Issue and threat

Issue #335 documents potential loss of measurements when the
installed updater recovery replaces current SQLite with a valid but
older archive. SHA256 and quick_check show historic archive integrity,
not that it contains every committed current sample or metadata entry.

## Proposed QA-only code change

The candidate adds scripts/pending-recovery-history-guard.py.
It reads current and archived SQLite and compares the complete
logical SQL dump, user_version and application_id, including
committed WAL entries. It verifies quick_check on both databases,
rejects missing or linked input and caps gzip expansion at 512 MiB.

The candidate recover_pending stops the configured Compose service,
then checks parity **before** changing image/deployment metadata.
It checks again after metadata and public-version operations,
immediately before clearing the pending marker.

If history differs, it fails closed and preserves current SQLite and
the pending marker. A config copy or version publication failure also
leaves that marker, so the operation remains retryable and auditable.

If both histories match, only deployment metadata is recovered.
**It never replaces the SQLite file or removes WAL/SHM**, which avoids
overwriting the latest database with a historical backup.

The guard extracts a private temporary archived copy and deletes it.
It does not provide an install, mutation or rollback command;
parity alone never authorizes a production release or Telegram PHOTO.

## Isolated acceptance

The dedicated tests cover older archives, equal-count changed voltage,
new committed WAL rows, altered outage/schema/metadata/header,
corrupt or truncated gzip, missing/hardlinked/symlinked SQLite,
decompression bounds, concurrent changes during comparison and
temporary file cleanup.

Legacy update interruption and rollback tests were corrected because
their former expectations deliberately rewound new measurements.
They now expect BLOCKED with pending marker and metadata preserved.
When the archive is exactly equal, tests verify inode-preserving
recovery of the previous image metadata without replacing SQLite.

Failures of configuration copy, version publication and a simulated
new measurement between the first and final checks leave the
pending marker and current data intact. A separate subprocess is
SIGKILLed during a mocked config copy: the pending marker remains,
SQLite is unchanged, and a subsequent isolated recovery retries
successfully. Missing live SQLite also fails closed.

The guard begins a SQLite read transaction so quick_check, the full
logical dump and header flags are read in one consistent snapshot;
it checks parity again immediately before pending-marker removal.

No production Docker, systemd, SQLite or Telegram operation occurs
during these tests.

## Outstanding production gates

- **All real restart and writer owners are not yet fenced.** Docker
  unless-stopped, active Compose, boot recovery and updater timer
  require separately verified quiescence during approved maintenance.
- The double history check narrows a race but cannot eliminate
  writes after the last comparison. The database is not overwritten,
  but concurrent schema changes could affect image compatibility.
- True physical host power-loss and boot-loop durability are not
  established by these isolated tests.
- Signed OCI runtime is 0.5.21-hw while installed host source
  remains 0.5.15-hw. An administrative production installation
  was previously denied. Successful preflight is not authorization.
- Fresh writer-fenced current-history rescue and exact A/B source
  plus SQLite backups, owner authorization and Telegram channel/pin
  continuity are still required for any approved production changes.

Do not merge or deploy solely on CI success. Independent review and
explicit owner approval remain required before live installation.
