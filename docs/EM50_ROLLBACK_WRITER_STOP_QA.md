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


## Private on-disk SQLite restore temporary files

The rollback path previously used predictable DB.restore.PID names
and a shell redirection which could create a readable mode-0644
SQLite copy when the invoking shell had umask 022. Changing the mode
after extraction does not protect the contents while gzip runs.

Rollback now creates a unique exclusive mode-0600 temporary file
with mktemp on the target filesystem. The EXIT trap deletes it on
decompression or validation failure, and the target DB is renamed
only after SQLite integrity verification and the writer guard.
Synthetic rollback fixtures explicitly run with umask 022 and
inspect the output file descriptor before gzip writes. Another
fixture simulates a partial gzip write followed by an error and
checks both deletion and preservation of the prior database.
No live rollback or database replacement is authorized by QA.


## Missing Compose file must not disable writer inspection

The rollback previously enabled its Docker writer check only if
the source tree still contained compose.yaml. When that file was
missing but shelly-voltage-bot remained running, rollback could
skip stopping and checking the writer and proceed to overwrite
SQLite. Synthetic RED fixtures reproduced this problem.

The fixed stop gate always requires a verified stopped Docker
writer before touching source files or the database. If the
Compose file exists, stop it first. If it is missing, require
the existing container to already report stopped; missing Docker
inspection or an unknown state is a blocker, not authorization.
The same stopped-state check remains active through database
replacement. No real Docker lifecycle action is done in QA.
