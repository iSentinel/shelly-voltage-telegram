# EM-50 predeployment SQLite backup parity — fail-closed QA

An everyday, SHA256-valid daily backup is not automatically suitable
for rollback during a new release. The running monitoring database
can contain more recent power measurements, outage closures and
Telegram status metadata than the latest nightly archive.

This read-only-to-production guard is independent of the routine
backup-integrity check. It requires an already existing snapshot;
it never creates or overwrites one.

    python3 scripts/em50-predeploy-backup-guard.py --help
    python3 scripts/em50-predeploy-backup-guard.py --json

By default PASS requires the newest voltage-*.sqlite3.gz in
/var/backups/shelly-voltage to be no older than 30 minutes, have a
matching SHA256 sidecar, pass SQLite quick_check in a disposable
private extraction directory, and match the live SQLite in these
critical fields:

- Measurement count AND the last measurement's full tuple.
- Latest outage identifier, start/end timestamps and phase.
- Saved Telegram status-message ID, compared in memory and
  NEVER emitted or stored in the report.

The online database is opened using SQLite mode=ro and never copied
or changed. The gzip expansion limit is 128 MiB, and symlink or
hardlink archive/manifest files fail. Temporary restored data is
deleted automatically.

The tool checks critical rollback continuity, not all possible
application state. Even a PASS is not a cryptographic guarantee
of full snapshot parity or ongoing safety: new measurements can
arrive immediately after the check. A real release still requires
an approved live pre-upgrade snapshot, rollback plan and
operator-controlled maintenance.

## Real Gateway finding, November 8

The newest daily archived SQLite was SHA256-valid, had 25,889
measurements and was about 8.9 hours old, while the running
database had 27,223+ measurements following a five-hour loss
of Shelly connectivity and its recovery. The new guard correctly
returned FAIL for stale age, missing measurements, changed outage
state, and changed status-message metadata.

No backup, container or production DB was changed. A user-approved
predeployment snapshot must be created close to release time and
this guard rerun on the updated snapshot; do not silently promote
the previous daily backup.

## Synthetic regression

    PYTHONPATH=.:tests python3 -W default -m unittest tests/test_em50_predeploy_backup_guard.py -v
    PYTHONPATH=.:tests python3 -W default -m unittest discover -s tests -q
    scripts/quality.sh

The command does not send Telegram messages, alter Shelly,
restart Docker or grant 0.5.14-hw release authorization.
