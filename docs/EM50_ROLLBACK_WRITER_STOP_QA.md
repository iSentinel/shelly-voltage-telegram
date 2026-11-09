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


## Preserve local checkpoints while restoring application sources

The source packagers deliberately exclude active ROADMAP, local
checkpoint backups, private QA progress and lock files. Restoring
a release must not delete them from the installed project or
overwrite their current content with obsolete entries in an old
archive.

The rollback source cleanup and tar extraction both preserve
ROADMAP.md, ROADMAP.md.pre-* checkpoints, the .work-state
directory and the two private operator/Telegram acceptance locks,
alongside existing .env, secrets and validation protections.
Synthetic rollback regression fixtures seed different current
and archived values and require that the current values survive
both cleanup and extraction. No real rollback is executed in QA.


## Incomplete source archive must fail before stopping Docker

A source archive can have valid SHA-256 checksums, a valid TAR
format and a valid archived SQLite database while omitting the
files needed to run the restored application. Such a snapshot
previously passed initial validation and could cause the rollback
to erase installed application code before discovering the issue.

The pre-stop TAR validation now requires nonempty VERSION,
app.py, Dockerfile, compose.yaml, requirements.txt,
scripts/backup.sh and the Compose/backup systemd unit entries. The check happens before writer
shutdown, source removal or SQLite replacement. It complements
archive member type, traversal and expansion-size checks.
A synthetic negative fixture supplies a checksum-valid
VERSION-only TAR and verifies fail-closed behavior; a complete
synthetic archive retains the original successful rollback path.


All five local upgrade source snapshots include both
systemd/shelly-voltage-compose.service and
systemd/shelly-voltage-backup.service. The optional historical
scripts/start-compose.sh exists in only one of five and is not
required. Negative fixtures now remove each of the eight required
paths individually; all must fail before Docker or SQLite changes.


## Reject duplicate logical TAR paths before source replacement

The earlier required-file check only collected nonempty file names.
An otherwise complete TAR could include a second empty ./app.py
entry after a valid ./app.py entry. Python counted the valid file,
but GNU tar would later extract the empty duplicate over the first.

The pre-stop verifier now rejects all repeated normalized member
names, including a path represented with or without an initial
./. This also protects optional source files from duplicate
replacement. A synthetic, SHA256-consistent TAR with a second
empty app.py entry must be rejected before Docker or SQLite
actions. Historical snapshot names were checked and have no
duplicate entries.


## Bind SHA-256 manifests to exactly the restored archive

The previous rollback code ran sha256sum -c on an archive
manifest. That command can return success when the manifest
names and hashes an unrelated file, leaving the actual selected
source or SQLite archive unchecked. The source TAR may still
be well-formed and the SQLite backup may still be readable,
but neither would be cryptographically bound to its manifest.

The rollback pre-stop verification now computes sha256sum on
the exact selected archive pathname and requires its manifest
to contain the corresponding single matching line. Symlinked
manifests are rejected. Synthetic negative fixtures substitute
a manifest of a decoy source TAR or SQLite gzip and require a
block before any target source or SQLite changes.

All five historical on-host upgrade snapshots were found
to have exact manifests for their selected source TAR and
SQLite archive. This check does not authorize their restoration.


## Require one unambiguous SQLite backup per snapshot

The previous rollback selected the first matching voltage SQLite gzip
using find piped to head, independently at validation and restoration.
A snapshot containing two individually valid archived databases was
accepted, leaving the selected history dependent on filesystem order,
and potentially changing between the two phases.

Both validation and decompression now use a shared selection function.
It refuses zero or multiple regular SQLite archives before source
removal or database replacement. Names are transferred as NUL-delimited
paths so whitespace cannot change the selection. A synthetic RED
fixture demonstrates the previous acceptance of two SHA256-valid
SQLite backups; its GREEN counterpart requires failure before
Docker/source/SQLite changes. All five existing local upgrade
snapshots contain exactly one matching archive.


## Refuse source TAR symlinks outside the selected snapshot

A source.tar.gz link pointing to an external archive previously
passed the SHA256 manifest gate when the manifest was generated
for the linked path. That could make a rollback read application
code from outside the inspected snapshot directory.

Preflight now requires the selected snapshot directory and
source.tar.gz to be non-symlinked, with the TAR a regular file.
The SQLite archive selector already excludes symlinks and
the SHA256 manifest validator rejects linked checksum files.
A synthetic RED fixture with a valid external TAR symlink
was accepted before the fix; GREEN must reject it before
service changes. The five real upgrade snapshots use
regular nonlinked source TAR files.
