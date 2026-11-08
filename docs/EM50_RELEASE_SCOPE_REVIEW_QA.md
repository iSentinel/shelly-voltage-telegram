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
