# Read-only backup validation

`scripts/verify-backups.py` is an administrator tool included in the
installed production `0.5.13-hw` test build.

The tool validates retained daily backups without stopping Docker,
modifying SQLite production data, extracting files into the live
database directory, or invoking the rollback script.

## Run the verification

From the source directory after the verifier is installed:

```bash
python3 scripts/verify-backups.py --help

python3 scripts/verify-backups.py \
  --backup-dir /var/backups/shelly-voltage \
  --count 3 \
  --max-size-mib 2048
```

The last command checks the three most recently modified
`voltage-*.sqlite3.gz` archives. The operator can choose a smaller
unpacked size limit when the SQLite database is small.

For machine-readable acceptance, add `--jsonl`. It emits exactly
one JSON object on standard output with `status`, `passed`, `checked`,
`requested`, and `read_only`. Diagnostic messages remain on standard
error. The same exit codes apply, and file paths are never included in
the JSONL record. Nothing is scheduled automatically.

To detect stale archives, opt in to an age limit. For example,
`--count 1 --max-age-hours 36` checks that the newest backup was
created within the last 36 hours. Existing default checks do not
reject old backups. If more than one archive is selected, the age
limit applies to **every selected archive**. Invalid, future-dated
or too-old archives fail with exit code 2; this checks file timestamps,
not whether Telegram messages were delivered. No schedule is enabled.

For **daily backup retention**, combine newest-copy freshness with
integrity checks for all retained copies. For example:

```bash
python3 scripts/verify-backups.py \
  --backup-dir /var/backups/shelly-voltage \
  --count 3 --max-age-hours 36 --max-age-newest-only --jsonl
```

This opt-in mode checks whether the **most recent** archive is
within 36 hours, while still verifying the checksums and SQLite
integrity of all three selected archives. Older copies are not
rejected solely because of their age. Without
`--max-age-newest-only`, the existing all-copies age policy
remains unchanged. Using the new flag without
`--max-age-hours` is an error (exit code 2).

The verifier checks the corresponding `.sha256` file before extracting
each archive, limits the size of decompressed data, restores it into an
automatically removed temporary directory, runs
`PRAGMA quick_check`, and counts retained measurements. It never
uses a network connection or contacts Telegram or Shelly.

The summary is `PASS: N/N backups validated` with exit code `0`
only when **all requested copies** pass. A missing archive, missing
checksum, checksum mismatch, bad gzip, broken SQLite database, or
exceeded size limit results in `FAIL` and exit code `2`.
An unreadable backup directory fails explicitly with its exception type
(for example, `PermissionError`) instead of appearing to contain no
archives. The operator must use a read-only account with access to the
backup directory; do not make private backups world-readable.
Symbolic links are not treated as backup archives or checksum sidecars.
When a SHA256 sidecar specifies a filename, its basename must
match the archive being validated. Existing absolute-path and
checksum-only sidecars remain supported; no referenced path is
opened from a sidecar line.
The script prints stable result labels rather than archive names or
private filesystem contents.

## Offline restoration rehearsal (candidate source only)

A checksum and `PRAGMA quick_check` do not establish that the monitor
can restore the most recent reading and outage state. The staged source
candidate additionally includes `scripts/restore-drill.py`. This command
is **not available in the deployed 0.5.13-hw container** until a future
explicitly approved release; run it from an isolated source checkout
with the pinned Python runtime requirements installed.

Run the dry/help mode to check supported arguments without touching
the database or services:

```bash
python scripts/restore-drill.py --help
```

Rehearse the three newest saved copies from an authorized host with
read-only access to its backup directory:

```bash
python scripts/restore-drill.py \
  --backup-dir /var/backups/shelly-voltage \
  --count 3 --max-size-mib 64 --jsonl
```

The command validates each archive's SHA-256 sidecar, enforces a maximum
decompressed size, and creates a separate disposable SQLite copy.
Before each extraction it also checks free space **on the temporary
filesystem**, requiring the configured maximum decompressed size plus
32 MiB safety headroom. If this capacity is unavailable, the drill
returns FAIL before unpacking anything. A capacity check cannot prevent
other processes from consuming disk space concurrently, so the
maximum size should remain conservative; for the current small
database, `--max-size-mib 64` is appropriate.
When a v1/v2 backup predates the three-phase `phase` column, the
restore drill first verifies the original restored SQLite and then
runs the application's supported schema migration **inside that
disposable copy only**. It verifies the row count and most recent
L1 timestamp/voltage after migration; the source gzip and live
database remain unchanged. Old archives with verified checksums
are not falsely rejected just because their schema predates v3.

The restore drill explicitly discards all inherited environment
variables when creating its test monitor. A user's live Shelly
address, password-file path, Telegram token, chat ID, and phase hosts
can never affect this test configuration; `SHELLY_HOST` is replaced
with the reserved documentation address. A backup with a
future/unsupported schema is classified as FAIL without a crash
or attempting a downgrade, while the source archive remains intact.

The disconnected monitor uses the reserved test address `192.0.2.1`,
empty Telegram credentials and no administrators. It restores the
last valid L1 timestamp and voltage and requires `/ready` semantics
to remain **503** before any fresh live poll. There are no Shelly
requests or Telegram messages, and it never writes the production DB.

The last standard-output line in `--jsonl` mode is one object containing
`status`, `requested`, `passed`, `network_calls`, `read_only_source`
and `temporary_copies_removed`. A fully successful run returns
exit code **0**; missing files, checksum mismatch, corrupt SQLite,
decompression limits and other failed verification return **2**.
The command's own log messages go to standard error.

This is a **source-level recovery smoke test**, not a service deployment,
actual restoration of the production database, physical Shelly acceptance
or proof of Telegram message delivery. It cannot replace the separately
approved `scripts/rollback.sh --dry-run`, the live outage baseline
`scripts/recovery-acceptance.py`, or the 15-minute stable restoration
check. Do not mistake an offline connectivity incident for a proven
mains outage.

## What validation proves

Checksum checks establish that an archive still matches its recorded
digest. SQLite integrity checks show that the decompressed snapshot
can be opened and that the `measurements` table is readable. Neither
check alone proves that every Telegram message was delivered or that
the latest SQLite state is backed up.

The daily systemd backup timer and backup destination remain
separate from this tool. Read-only inspection can check a copy while
the physical Shelly meter is offline, but a candidate application
release must not be promoted until the hardware acceptance and
rollback gates are satisfied.

For an actual rollback, use the existing documented, explicitly
approved `scripts/rollback.sh` procedure. Never perform a rollback
as part of verification.
