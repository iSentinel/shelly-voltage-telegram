# Issues 340/422/431: bind disposable signed rollback to exact old Git SHA

Date: 2026-11-20 (Europe/Kyiv).
Scope: source-only QA. No live update or release.

## Gap closed

The legacy signed-rollback-pair-preflight.py had already been
strengthened to require --expected-commit when checking the old
OCI signature. But the caller
scripts/signed-rollback-disposable-rehearsal.py continued calling
check_pair() without that argument, preventing a real
signature-verified rehearsal from ever passing even with a
known immutable old source revision.

The disposable rehearsal now exposes --expected-commit and
passes it unchanged to the old OCI image verifier. If
--verify-signature is selected, the caller first rejects a
missing, abbreviated or mutable commit ref. These failures
happen before creating any synthetic SQLite/PHOTO fixture.

No tag or version string is treated as a source Git SHA. The
identity-only mode remains available but its result does not
assert a verified signature, and both report paths explicitly
keep production_modified=false, release_authorized=false and
rollback_authorized=false.

## Acceptance

- Isolated LXC114 offline focused rollback tests 66/66 PASS.
- Missing SHA and mutable refs: blocked before loading verifier
  or creating failure-injection files.
- Correct full source SHA: forwarded to check_pair as the
  required expected_commit keyword.
- Error from signed image verification: blocks before fixture
  creation; no unsafe fallback.
- Ruff0.14.2, project maintainability and formatting PASS.

These are QA-only generated SQLite and mocked signature paths,
not real cryptographic verification of a new source+OCI pair.
The old public v0.5.25-hw source TAR Sigstore bundle remains
absent; actual cross-version signed update and rollback preserving
the newest WAL, PHOTO and Telegram intent history remains
unaccepted.

## QA-only CLI

    python3 scripts/signed-rollback-disposable-rehearsal.py --help

For a future REAL signed old-OCI check, supply both
--verify-signature and --expected-commit with the exact old
40-hex SHA. Do not run this fixture on LXC110 production data.
