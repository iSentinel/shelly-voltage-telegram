# General runtime failure recovery contract

Status: review proposal and synthetic QA evidence, not an approved
production recovery procedure. Applies to the future Report Worker
release track, not the immutable narrow .29 review.

## Recovery boundary and decisions

| Observed boundary | Recovery route to review |
| --- | --- |
| Preboot, candidate proved never able to write | Existing exact previous-pair recovery, without DB replacement |
| Preboot, unresolved delivery | Retain intent, review previous-pair monitoring only, no acceptance |
| Candidate may have run, report-only failure | Same signed runtime, protected paused-mode transition |
| Candidate may have run, general or unknown fault | Fenced hold and separately qualified forward repair |
| Corrupt DB, missing evidence, foreign owner | Incident hold, retain evidence, no automatic restore/start |

Do not infer preboot from an absent container, zero StartedAt on a
replacement, or a failed CLI response. Verify the durable cutover
journal, exact installed and previous pair, ownership, locks, and
whether any candidate writer could have run. Uncertainty must be
treated as candidate-may-have-run. A STOP/START operation must use
the existing separately reviewed maintenance and owner gates.

After the candidate could have written, the retained pre-cutover
database is evidence, not a database to restore over newer history.
Retain the current database, WAL and related report/status intent
state, plus a verified fresh rescue, before deciding on recovery.
An unchanged row count does not prove unchanged data or metadata.
If the database cannot be opened safely, preserve its bytes and
sidecars for a separate incident review. Do not delete markers,
force checkpoint, replace the live inode or silently rewind history.

## General fault recovery proposal

Prefer a separately approved forward repair retaining the report
reservation semantics and database compatibility of the installed
runtime. PAUSED is only useful for a confirmed report-only fault.
Require an explicit fault classification and evidence, exact signed
repair source/OCI pair, reviewed schema and metadata compatibility,
and a tested stopped-writer transaction preserving the current DB.
For any changed runtime, qualify the repair with synthetic hardware,
ordinary status and report delivery, unresolved ACKs, crash/restart
and rollback/forward recovery boundaries. Prove current history and
PHOTO preservation, sole writer, exact configuration and readiness.
No compatibility exception to strict existing rollback is introduced.

If no qualified repair exists, keep a separately authorized incident
hold rather than guessing a safe downgrade or retrying indefinitely.
The hold may interrupt monitoring; it is not an instruction to stop
a healthy production service. No new production operator is provided.

## Read-only synthetic reviewer

qa-general-recovery-review.py compares two distinct standalone
SQLite copies using full logical dump hashes, user_version,
application_id, measurement count and unresolved intent count.
It rejects nonregular or linked paths, sidecars, failed quick_check
and changed file identity. The CLI restricts inputs to a protected
offline LXC114 fixture; it uses immutable read-only SQLite access
only for those standalone files. It never starts Docker, creates a
backup, restores data, or changes a production journal.

The phase, fault classification and parity flags are supplied by
the caller, not independently authenticated. The decision is a
review route only. Every authorization flag is false, including
when all compared data match. Logical dump equality is not a
cryptographic receipt or proof that all possible live state is
captured. Production WAL acquisition and a durable hold/repair
operator are still unimplemented.

## Evidence, 2026-11-27

Exact #495 and narrow #472 CI 8/8 PASS. 180 focused tests and
quality gate PASS, including nine new tests with actual synthetic
SQLite files, modeled host guard, stale history, metadata-only
drift, sidecar, corrupt file and link rejection.

Real offline LXC114 fixture /var/lib/svt-general-recovery-qa.2vdOQ0:
- preboot: three rows in both copies, logical parity, no pending;
- candidate-may-have-run: four live rows, three rescue rows,
  one unresolved synthetic pending intent, forward-repair/hold
  review, stale rescue detected;
- report-only: same evidence, paused review route, no authorization;
- both file SHA256 unchanged by the postboot reviews; no Docker start.
Result PASS_REAL_OFFLINE_RECOVERY_REVIEW_ONLY.

Next: review evidence and requirements for a forward repair
candidate and incident-hold ownership, independently bind real
signed pair and journal evidence, qualify integrated QA when
available. Previously blocked integrated execution not repeated.
Production remains .27; no migration, signing or deployment.
