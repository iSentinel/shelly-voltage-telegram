# EM-50 0.5.14-hw source scope audit (QA-only)

The candidate spans runtime, Telegram, DTEK/YASNO integration,
deployment templates, source release and rollback scripts, tests and docs.
A passing CI run does not constitute review of changed code or authorize
promotion. Use the built-in Git diff to produce a consistent list of files
outside tests/docs that require explicit module-owner review.

    python3 scripts/em50-release-scope-audit.py --help
    python3 scripts/em50-release-scope-audit.py --base origin/main --head HEAD --json
    python3 scripts/em50-release-scope-audit.py --enforce

The normal audit returns exit code 0 when it can describe the diff;
--enforce exits 2 to indicate outstanding review. Metadata failures,
private paths, malformed records, oversized diffs and invalid revisions
exit 1. The output intentionally never claims a release is authorized
and does not inspect file content, environment secrets or user data.

Review owners must separately examine runtime changes, network/provider
sources, Telegram status/notifications, Docker and systemd permissions,
SQLite preservation, installer/rollback behavior, provenance and signed
artifacts. Record approvals outside this tool; do not add an automatic
release-approval override or bypass physical acceptance.

No Git push, PR merge, Docker update, systemd activation, Telegram send,
Shelly operation, or production SQLite access is performed by this tool.

## Clean-tree prerequisite

A review of committed changes alone is incomplete if the QA tree has
local edits or new untracked source files. The audit now checks the
complete Git working tree before collecting a release manifest,
including staged, unstaged and untracked files. Any such change
returns exit code 1 without disclosing private filenames; the
candidate must first be committed to a reviewable branch.

A deliberately uncommitted multi-PR integration worktree can still
run Python tests and quality checks, but **must not** be presented
as having a complete source-scope manifest. Use a clean, reviewed
candidate Git commit for authoritative release-scope evidence.
Neither the normal audit nor --enforce approves a release.
