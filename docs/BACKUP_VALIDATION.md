# Read-only backup validation

`scripts/verify-backups.py` is a source-candidate administrator tool.
It will only be available with a future approved release; installed
production `0.5.12-hw` does not yet contain it.

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
