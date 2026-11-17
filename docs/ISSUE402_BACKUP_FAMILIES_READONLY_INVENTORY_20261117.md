# Issue #402 — read-only backup-family inventory

This diagnostic **does not** replace or bypass the strict
em50-release-preflight.py backup parity requirement. It distinguishes
a restorable historical archive from a fresh **writer-fenced, full
current-state** snapshot required immediately before future cutovers.

## Evidence from real LXC110 archives, 17 November 2026

Read-only source: production SQLite in WAL mode. Four existing local
backup families were scanned. All four checksum sidecars matched the
archive SHA256, and each archive was decompressed and SQLite-validated
independently in a disposable private directory:

| Family | Saved L1/history rows | Lag at 19:53 Kyiv | SHA + restore |
| --- | ---: | ---: | --- |
| periodic nightly | 84,441 | 2,822 | PASS |
| explicit cutover | 86,756 | 507 | PASS |
| updater recovery | 86,793 | 470 | PASS |
| installed-source upgrade | 86,814 | 449 | PASS |

At that read-only checkpoint there were 87,263 live measurements.
**All four current-state parity results were FAIL.** The periodic
archive was about 625 minutes old; the others were 76–86 minutes old.
This is expected for a healthy continuously writing monitor, not
evidence of archive corruption.

The original signed release v0.5.22-hw had already made a
writer-fenced, fully parity-checked snapshot during its own cutover,
with independent disposable restore acceptance. These historical
checks do not authorize a different future cutover.

## Tool behavior

The new script em50-backup-families-audit.py accepts explicit named
families: periodic, cutover, updater and source_upgrade. It imports
the same existing whole-history guard, verifies each SHA256 checksum,
decompresses each archive within bounded disposable storage, checks
SQLite quick_check, and compares complete records, outage state and
persistent Telegram status metadata to one point-in-time live read.

The output never includes Telegram message IDs or private values.
It never claims a source archive to be writer-fenced merely because
the directory name says cutover or updater. Missing, linked,
corrupted, oversized, malformed and stale archives are reported
without inventing successful release status.

The result PASS_ARCHIVES_ONLY means **all requested archive copies
are internally valid**, even if current parity FAIL. If any archive
cannot be verified, status is FAIL_ARCHIVE_INTEGRITY and exit code
is nonzero. Every outcome contains release_authorized=false,
fresh_writer_fenced_backup_created=false and
source_offhost_independence_verified=false.

## Sample read-only invocation

    python3 scripts/em50-backup-families-audit.py       --db /var/lib/shelly-voltage/voltage.sqlite3       --snapshot periodic=/var/backups/shelly-voltage       --snapshot cutover=/path/to/approved/cutover-dir       --snapshot updater=/path/to/updater/snapshot-dir       --snapshot source_upgrade=/path/to/upgrade-dir       --max-age-minutes 30 --jsonl

Use only exact existing directories; no source archive, secret,
Telegram message, Docker container or production state is changed.

## Safety and ownership

A separate production release requires a new owner authorization,
signed exact-source OCI, writer fence, new entire-database snapshot
and independent disposable restore check. The user has approved the
single-NVMe catastrophic-loss failure domain; this remains one
physical medium, **not off-host** redundancy. The public Telegram
PHOTO gate is separate and remains disabled.
