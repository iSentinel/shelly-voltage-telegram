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
