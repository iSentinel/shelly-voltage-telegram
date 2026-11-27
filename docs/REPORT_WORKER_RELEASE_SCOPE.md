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
