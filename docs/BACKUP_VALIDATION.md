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

The verifier checks the corresponding `.sha256` file before extracting
each archive, limits the size of decompressed data, restores it into an
automatically removed temporary directory, runs
`PRAGMA quick_check`, and counts retained measurements. It never
uses a network connection or contacts Telegram or Shelly.

The summary is `PASS: N/N backups validated` with exit code `0`
only when **all requested copies** pass. A missing archive, missing
checksum, checksum mismatch, bad gzip, broken SQLite database, or
exceeded size limit results in `FAIL` and exit code `2`.
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
