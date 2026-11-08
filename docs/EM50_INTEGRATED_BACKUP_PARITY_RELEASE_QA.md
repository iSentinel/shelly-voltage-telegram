# Integrate strict predeployment backup parity into EM-50 release readiness

The previous read-only release preflight verified routine backup SHA256
and age (within 26 hours), but could report all green checks while
the newest daily SQLite backup was missing more than a thousand
recent Shelly measurements, the latest connectivity outage closure
and Telegram status-message state.

The candidate release preflight now includes a distinct automatic
predeploy_backup_parity gate, alongside routine backup-integrity
checks. It reuses the read-only scripts/em50-predeploy-backup-guard.py:

- SHA256 and SQLite quick_check of the existing newest backup.
- Backup age no more than 30 minutes.
- Exact critical-state parity: measurement count and last tuple,
  latest outage metadata and saved status-message ID.
- Bounded, automatically deleted QA copy of the compressed database.
  Never copies or writes the production SQLite.

This makes the final candidate gate correctly FAIL when an old
nightly backup is intact but cannot represent the newest data.

    python3 scripts/em50-release-preflight.py --help

    python3 scripts/em50-release-preflight.py --json \
      --cache /tmp/shelly-yasno-handoff-20261108/yasno.json

The source candidate remains explicitly unpublished; the CLI never
returns release_authorized=true. Individual automatic failures
return exit 1. Full automatic PASS with manual gates pending returns
exit 2. The default private Telegram client receipt, YASNO service
account/mount activation and explicit release/rollback authorization
remain independent requirements. Any outdated YASNO QA cache can
also correctly fail if its retrieval window has elapsed.

A fully matching snapshot does not prove entire database bitwise
equality or future safety: writes may arrive immediately after the
audit. Operator-approved deployment still requires a close-to-release
verified rescue backup and controlled rollback procedure.

Regression:

    PYTHONPATH=.:tests python3 -W default -m unittest tests/test_em50_release_preflight.py -v
    PYTHONPATH=.:tests python3 -W default -m unittest discover -s tests -q
    scripts/quality.sh

No Telegram API writes, Shellly device changes, Docker restarts,
service installations, source promotion or production SQLite writes.
## Complete-history parity is mandatory

The integrated release preflight invokes the current
scripts/em50-predeploy-backup-guard.py from this exact source
candidate. That guard now hashes every historical measurement
and outage row, not merely the latest item/count. Two isolated
tests reject older row corruption; a separate integration test
confirms the **main** release gate also fails on that mismatch.

On the live 0.5.13-hw monitoring server (Shelly ONLINE), the
read-only release preflight reported five automated PASS checks
but FAIL for predeploy_backup_parity. This is expected with the
older nightly backup. The test did not create or overwrite any
backup, restart a container or change the Telegram channel.


## Persisted SQLite header parity

The full logical SQL dump previously omitted SQLite header fields
PRAGMA user_version and PRAGMA application_id. A copy could have
identical measurements, outages, Telegram status, settings and schema
SQL yet a different persisted migration/application identity, and
be incorrectly called equivalent.

The whole-state SHA-256 fingerprint now includes both integer header
pragmas, with names and length-prefixes but no disclosed table rows or
Telegram identifiers. Both the release backup parity guard and
predeploy online-snapshot capture share this fingerprint.

Four synthetic RED/GREEN regression tests cover mismatched saved
headers and live header drift during SQLite online backup and gzip
compression. On drift, the private snapshot fails closed and no archive
is published. No production schema or database is modified.
