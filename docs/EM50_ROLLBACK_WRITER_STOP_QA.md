# EM-50 rollback writer-stop prerequisite (QA)

The rollback CLI must not extract source archives or replace SQLite while
the existing Docker writer is running. Previous code ignored Compose stop
errors; a synthetic Docker failure could silently proceed to restoration.

The amended rollback stops Compose before capturing its rescue copy,
then requires Docker inspect to report the named writer container
Running=false. It checks again after rescue and immediately before
SQLite WAL cleanup and replacement. Verification remains required even\nif restored source files no longer contain compose.yaml. Docker stop errors, missing state,
and still-running writers all fail closed without beginning destructive
restoration. A production rollout additionally requires an externally
reviewed quiescence/cutover and complete-history parity check: this
Docker-state check alone cannot detect other SQLite writers or races.

The existing explicit --confirm-db-replacement flag remains mandatory
for real replacement of a live database. All rollback operations remain
separate from release authorization; this patch does not stop
production or approve an actual rollback.

Verification with synthetic temporary filesystem fixtures:

    bash -n scripts/rollback.sh tests/test_rollback.sh
    bash tests/test_rollback.sh
    PYTHONPATH=.:tests python3 -W default -m unittest discover -s tests -q
    scripts/quality.sh

No actual Docker container, live SQLite database, secrets, Telegram
message or Shelly device is changed by these tests.
