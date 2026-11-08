# EM-50 protected predeployment snapshot window — QA only

This script prepares a versioned SQLite snapshot using the built-in
SQLite online backup API from a read-only source connection. It is
not an installer, database restore, or permission to publish 0.5.14-hw.

The existing nightly backups have valid checksums but cannot represent
the currently running database: physical measurements and outage
history have advanced, and Telegram status metadata may differ.

## Dry-run: safe on the actual LXC, no writes

    python3 scripts/em50-predeploy-snapshot.py --help
    python3 scripts/em50-predeploy-snapshot.py --dry-run \
      --db /var/lib/shelly-voltage/voltage.sqlite3 \
      --output-dir /var/backups/shelly-voltage

The destination must already exist, belong to the invoking account
and have mode 0700. Symlinks/hardlinks for source and symlinks in
the destination path are rejected. Dry-run reads file metadata only.

## Future explicitly approved snapshot: NOT executed on production

Example after an operator authorizes a controlled rollout:

    python3 scripts/em50-predeploy-snapshot.py --capture \
      --db /var/lib/shelly-voltage/voltage.sqlite3 \
      --output-dir /var/backups/shelly-voltage \
      --confirm EM50_PRIVATE_SNAPSHOT_ONLY

The command does NOT change live SQLite, stop/start a container, enable
services, touch Telegram or prune existing backups. It creates unique
versioned files:
- voltage-YYYYMMDD-HHMMSS-<random>.sqlite3.gz
- matching .sha256 with an exact absolute archive path

Both files are created with mode 0600 in an existing 0700 directory.
The intermediate SQLite snapshot is temporary and deleted upon
completion or failure. The complete measurements history, full outage
history, latest measurement and current Telegram status message
metadata are read from the snapshot and from the live database.
A difference either immediately after copy or after compression
aborts without publishing new files. Output never logs private
measurement rows or Telegram message identifiers.

**Time-of-check/time-of-use warning:** Production keeps receiving
measurements approximately every 10 seconds. A snapshot can be equal
at capture time and become out of date immediately afterwards. This
is NOT a guarantee that its contents match SQLite when a subsequent
deployment begins. An approved release must first coordinate a
short, reviewed writer-quiescence window (without triggering power
outage events), preserve a rollback rescue copy, and rerun the strict
full-history parity guard immediately before installation. If parity
fails, stop release; do not disable the guard or silently replace
newer SQLite history. A source-code-only QA test cannot approve a
live writer pause, installer run or source-data reset.

A restored older SQLite version may be incompatible with the newer
schema or missing latest outage/Telegram state. Explicit rollback
approval and a separate recovery plan remain mandatory.

## Isolated regression

    PYTHONPATH=.:tests python3 -W default -m unittest \
      tests/test_em50_predeploy_snapshot.py -v
    PYTHONPATH=.:tests python3 -W default -m unittest discover -s tests -q
    scripts/quality.sh
    bash tests/test_release.sh

The snapshot test creates only synthetic disposable SQLite files,
never a copy of the real user's database.
