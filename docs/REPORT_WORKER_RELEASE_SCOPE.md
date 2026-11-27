# Report Worker release scope and gate review

2026-11-27. This is a scope review, not an approval or a release.

## Separate release tracks

- Narrow v0.5.29-hw: PR #472, exact commit
  18b9380a39501730abca9756a4104d63506463d4. CI 8/8 PASS,
  unsigned source archive SHA256
  06ac8440bcb4555f59a73f495b6fdffe0ad22f23c0e0de3d76d29998724cc497.
  No new tag or approval. Preserve this exact identity.
- Report Worker stack: #482 exact head
  761da7d77d020aef95a52a988755e06d3887993d, CI 8/8 PASS.
  Compared with the published .28 commit, 35 changed files,
  6914 insertions and 95 deletions before this audit revision.
  The app.py runtime changes and supervisor changes require separate
  review. This is not an updater-only release.

## Scope categories

Runtime: app.py, .env.example, scheduled report worker, interruption,
reservation identities, pending intent handling, explicit recovery mode.
Operator guards: github_update.py, signed-forward-cutover.py,
supervised-container.py and reconciliation/voltage review tools.
QA: qa-report-mode-transaction.py and related runtime acceptance,
generation, signed pair, OCI and Compose provenance tools and tests.
Documentation: retain verified evidence and explicitly mark limitations.

Review QA-only tooling separately from supported runtime/operator
interfaces. Do not automatically promote QA entrypoints to production.
Do not assign a version or build a signed Report Worker release until
the activation and recovery contract is reviewed and tested. Existing
strict monitor input parity blocks a downgrade across changed app.py;
do not relax that guard to make the candidate appear rollback-ready.
A signed same-runtime recovery profile is still required.

## Installed production review

Production remains .27. A temporary read-only audit imported the
existing signed-forward guard functions, compared 34 signed build
and startup inputs byte for byte, verified the installed private
pair and verified transaction, retained source hash, exact systemd
unit recipes, exclusive enabled startup owner, no pending markers,
expected mounts, OCI revision and local RepoDigests. The existing
writer owner lock was observed held; no lock file was created.
Container/image/StartedAt and pair were bracketed before/after.
Result PASS_INSTALLED_SUPERVISOR_REVIEW_ONLY. No source or service
installation, restarts, or config/database changes. Temporary
audit code and dependency copy removed. The owner-lock probe does
not prove which specific process holds the lock. The observation
is not a lock-held atomic transaction, full signature reverification,
or electrical/Telegram deployment authorization.

Public receipt preflight remains false: existing legacy DynamicUser
state path is an independent cutover blocker. Do not alter that
path during audit or infer that a healthy monitor authorizes migration.
Existing narrow .29 review and release approval remain separate.

## Exact unsigned QA snapshot and runtime smoke (2026-11-27)

Exact #486 commit 24d5341392a1f2a7a4f374ef6e0276240b53201e
and narrow #472 CI 8/8 PASS. Two clean git archive + gzip -n
exports under different umasks were byte-identical:
SHA256 01f230c138dd6c0626c27a95e83df606b52162856b40e0d21be0c640dc39d9bc.
637 files; 29 compared build inputs including VERSION; only app.py
differs from the signed .28 archive, SHA256
b9eea01ae7c5072a1bec2a0449c70640b58e3ebee10b42247a12baf9ede125b8.
Evidence /var/tmp/svt-report-worker-candidate.iBVyZ0.

Offline LXC114 built a derived unsigned QA image on the cached .28
image ID using a local COPY-only Dockerfile, --network none and
--pull=false. Image ID:
sha256:6b3f263cb444ce6b7424fd14563c85cb5a82253ad8ac74eecb7469d0bbe38541.
All 26 Python/VERSION files inside matched exact snapshot bytes.
This is not a full release Dockerfile rebuild, signed OCI image,
or a new release version; VERSION remains .28. No tag was created.

In-image recovery-mode tests 5/5, daily retry 15/15, weekly digest
14/14. HTTP deadline suite initially 6/7: the slow-body test's
two-second synthetic budget expired before loopback server accepted.
An unchanged isolated retry passed. A test-only revision increases
the synthetic budget to five seconds, adds a deliberately longer
trickle response, and asserts elapsed <7s; production deadline
remains 12s. Complete revised suite inside the same image 7/7 PASS.
The cause of the first failure was not independently established;
cold startup/scheduling is a plausible explanation. 41/41 final
targeted in-image tests, with the revised deadline test distinguished
from the exact original snapshot.

Actual isolated runtime smoke using the existing synthetic meter and
loopback Telegram probe (original monitor methods plus test mocks):
paused 25 measurements per phase, readiness age .0086s, one ordinary
alert and zero reports, graceful exit 0 and SQLite quick_check OK.
Enabled on a separate fresh database: 34 measurements per phase,
readiness age .0429s, one synthetic report, graceful exit 0 and
SQLite quick_check OK. Both disposable containers removed. Evidence
/var/lib/svt-report-candidate-qa.z1sBHT. No production data or external
Telegram delivery. Not a same-database transition or signed cutover.
The next stage is the protected same-runtime transaction with
explicit owner-bound review, real same-database transition and
fault injection. No publication or production authorization.

## Same-database mode transition fault-injection evidence (2026-11-27)

Exact #487 and #472 CI 8/8 PASS. Actual isolated LXC114 fixture
/var/lib/svt-report-mode-tx-qa.xc0jjl used the previously qualified
unsigned QA image sha256:6b3f263cb444ce6b7424fd14563c85cb5a82253ad8ac74eecb7469d0bbe38541.
The one-off probe used real report reservation, delivery and monitor
methods with synthetic meter, loopback Telegram API and selected
initialization/maintenance mocks. The same database was retained
through three disposable container instances with the same image ID.

Initial enabled launch acknowledged one daily report and created a
weekly reservation using the actual reservation method. Three phases
ready; graceful exit 0. The initial stop script then aborted because
the inherited Docker logging driver did not support docker logs;
the container was removed, the original database and graceful marker
remained, and subsequent runs explicitly used json-file logging.
No initial launch was repeated.

The one-off transition script used existing operator, quiescent writer
and update-gate locks, a durable config/history journal and the exact
owned pending marker. Real SIGKILL (137) after atomic paused config
publication left the marker and prepared record. Recovery verified
the stopped history and exact old/new config, recorded configured
and cleared only its marker. Paused runtime reached readiness and
ordinary alert delivery; daily duplicate, pending weekly duplicate
and new weekly report were refused. Actual Docker SIGKILL (137)
then stopped the paused writer. The next stopped snapshot verified
all original 60 rows, inode and report metadata. A second protected
config switch restored enabled mode; actual resumed runtime refused
the previously delivered daily and pending weekly sends, acknowledged
a new weekly report, and stopped cleanly.

Rows: 60 -> 144 -> 204, final 68 per phase; SQLite inode 42590
and device 64542 unchanged. Exact earlier row-prefix hashes matched.
The paused metadata fingerprint matched initial; the final metadata
contains the original daily delivered marker, unresolved weekly
reservation and new weekly delivered marker. Five synthetic deliveries:
initial report, initial ordinary, paused ordinary, resumed report,
resumed ordinary. SQLite quick_check OK. All three phases ready in
each launch. Result PASS_SAME_DATABASE_MODE_TRANSITION.

This was a controlled QA rehearsal, not a reusable transaction operator.
The one-off scripts and journal are not an authenticated owner approval,
do not implement every crash boundary or startup authorization, and
the initial reservation was created without attempting HTTP (not an
actual lost ACK). No production data or Telegram traffic was used.
The unsigned image is not a signed feature-capable pair. The next
step is to formalize the transaction and test its recovery/fencing
boundaries, then qualify a separately approved signed candidate.

## Exact candidate scope review (2026-11-27 18:05 Europe/Kyiv)

Decision: NO_GO_UNSIGNED_SCOPE_REVIEW_ONLY. This is an exact
engineering review packet, not a release candidate approval request.

Reviewed Report Worker source: #493, commit
8f1df6da3bbba454e3345244c762a7f64e5b4a9c, CI 8/8 PASS, CLEAN.
Against signed .28: 55 files, +10755/-95 lines. Two git archive
exports with different umasks were byte-identical. All 648 archived
regular files checked individually against the exact Git blobs.
Unsigned review archive (not the release packaging format) SHA256:
0baf9e0a9bf6f9c78353e830d3ed0004e116b16b1092b07ede802583852f8afb.
Review directory /var/tmp/svt-report-scope-review.vyi5wm;
scope-review.json SHA256:
dbcc40f10d2a7b2d2b394ce0bc8cd76c59e708d9e8b2f334c582998e71aff312.

All 29 previously compared runtime/build inputs match the earlier
unsigned #486 smoke snapshot. Against signed .28, app.py alone
differs in that input set; its SHA256 remains
b9eea01ae7c5072a1bec2a0449c70640b58e3ebee10b42247a12baf9ede125b8.
This confirms byte continuity, not equivalence of complete installer,
supervisor, operator code, or independently rebuilt OCI images.
AST comparison of public_receipt_preflight and publish_version
against exact narrow .29 confirms those two functions match.
The additional report guards are distinct from narrow .29.

### Release decisions and remaining gates

| Gate | Status |
| --- | --- |
| Exact source review and CI | PASS |
| Earlier unsigned image smoke and same-database rehearsal | PASS, limited scope |
| New durable operator, launch, acceptance and delivery | Unit/CI PASS; integrated trial pending |
| Full release Dockerfile rebuild and signed source/OCI pair | NOT DONE |
| Signed same-runtime recovery and owner-bound production policy | NOT QUALIFIED |
| Legacy public receipt migration in production | BLOCKED |
| Strict rollback with changed app.py | NO-GO |
| Separate exact owner approval | NOT RECEIVED |

Do not assign a new release version, create a tag, sign, publish,
or deploy the Report Worker stack yet. Do not relax production's
default/enabled-only guard or the strict runtime parity check.
A combined integration command was previously blocked and was
not retried or bypassed. The next independent engineering task is
review of the recovery/rollback contract and candidate admission
criteria; complete integrated qualification when available.

The separate narrow .29 archive hash was reverified:
06ac8440bcb4555f59a73f495b6fdffe0ad22f23c0e0de3d76d29998724cc497.
Exact #472 CI 8/8 PASS; .29 and .30 tags absent. Its original
publication-only approval packet remains unchanged.

108 focused tests rerun and quality gate PASS. Read-only production
review: .27 Healthy, restart count zero, 86 measurements per phase
over 900 seconds, max gap 11.15s, all voltages in 207-253V,
zero unresolved report intents. This is a limited voltage/history
observation, not the full cutover authorization. Legacy public
alias still present, checker ExecMainStatus=200 (exit-code).
No production migration or restart performed.
