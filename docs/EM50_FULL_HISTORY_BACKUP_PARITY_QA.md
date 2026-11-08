# Full-history parity for EM-50 predeployment SQLite backups

This QA extension closes a blind spot in the earlier 0.5.14-hw rollback
preflight: matching row counts and the latest measurement/event alone
do not prove that historical samples are identical. A changed older
measurement or outage could otherwise pass those superficial checks.

The read-only `scripts/em50-predeploy-backup-guard.py` now compares
complete ordered measurements and outage events using SHA-256 digests
computed **only in process memory**. Inputs are serialized with a
stable length prefix and deterministic row ordering by primary key.
No measurement tuples, Telegram message IDs, digest bytes or private
state are printed in the result. The SQLite read is enclosed in one
read transaction so the table counts, digest, latest event and status
metadata derive from one consistent observation.

Two new tests:
- An older measured voltage is altered while the measurement count
  and latest measurement remain unchanged. Full-history parity FAILS.
- An older outage is modified while the latest outage remains
  unchanged. Full-outage parity FAILS.

Existing checksum, archive-symlink, file permissions, expansion limits,
outage closure and Telegram status-message metadata checks remain.

## Local reproducible QA

    PYTHONPATH=.:tests python3 -W default -m unittest tests/test_em50_predeploy_backup_guard.py -v
    PYTHONPATH=.:tests python3 -W default -m unittest discover -s tests -q
    scripts/quality.sh
    bash tests/test_release.sh

On November 8, the candidate suite returned 1230/1230 PASS,
Ruff/quality PASS, archive reproducibility and private-env
exclusion PASS. The current nightly backup was valid but NOT
sufficient for deployment: it contained 25,889 measurements
while production contained 27,718 during the QA check.
All full-history parity checks correctly blocked promotion.

A PASS is still a *point-in-time* observation, not permission to
deploy. Continuous live polling can create a new measurement
immediately after a snapshot and parity check. A future approved
deployment must take a fresh versioned snapshot, review this
freshness window and preserve rollback rescue data. It must not
silently replace SQLite history with an old archive.

This extension neither creates backups nor writes to production
SQLite, starts systemd, controls Shelly, or posts to Telegram.
All code remains an unpublished Draft candidate.
