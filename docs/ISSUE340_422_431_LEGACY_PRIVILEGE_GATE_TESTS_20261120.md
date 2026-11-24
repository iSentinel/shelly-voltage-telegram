# Issues 340/422/431: retain root guard in non-root QA

Date: 2026-11-20 Europe/Kyiv
Scope: QA test correction only, no production system changes.

## Observed CI failure

At exact QA SHA b190bf91ee9371794a9a16330ac28d27f7504fbc
GitHub Actions run 44645093536 reached a Python3.12 unit-test
failure. The new rollback guard test assumed a privileged shell
would reach the production-path gate and return exit 3. A normal
unprivileged GitHub runner first failed the existing require_root
check with exit 2 instead, which is the intended secure behavior.
Python3.13/3.11 CI matrix jobs were cancelled after the fail-fast
failure, NOT successful.

LXC114 non-root UID10001 integration surfaced the same two
test assumptions for legacy apply-update and rollback.

## Exact test-only correction

The two direct command-entry tests now require:
- when executing as root, the source-only production-path guard
  returns exit3 and the production SQLite/source rewind message;
- when executing without root, the earlier existing privilege
  guard returns exit2 and the root-required message.

Independent sourced-function policy tests continue requiring
exit3 for production SQLite/source canonical paths before any
fake Docker operation, including parent-hop aliases.

No bypass was introduced, and both old destructive production
guards remain unconditional for actual privileged calls.

## Evidence

- Both updated tests and negative cases: 14/14 PASS in QA LXC114.
- Offline isolated UID10001 Docker regression across outage
  cache/YASNO, protected writer, signed release, interruption
  and rollback: 586/586 PASS, no ResourceWarning.
- Ruff0.14.2, project source quality PASS.
- No running QA Docker container and no pending marker afterwards.
- No source or script installed to production LXC110.

A fresh exact-SHA GitHub matrix run is required after pushing
this test correction; prior failed CI does not count as green.

## Follow-up: CLI ordering and real rollback shell fixture

GitHub CI run 44646128701 of the earlier test correction failed
on Python3.11: the updater's production SQLite guard intentionally
runs BEFORE its root requirements, and therefore correctly
returned exit3 for the unprivileged GitHub runner. In contrast,
rollback.sh calls require_root before its production rollback
guard, returning exit2 when unprivileged.

The updater test now requires early refusal exit3 and production
rewind message; rollback command test continues to require root
refusal exit2 when unprivileged and production guard exit3 when
run as root. The actual safety guards are unchanged.

Full disposable tests/test_rollback.sh initially stopped after
ROLLBACK_FIXTURE PASS because a direct sourced restore_database
gzip fault-injection case did not set the temporary source root.
The new guard correctly detected default /opt/shelly-voltage and
refused early. The fixture now explicitly sets SVT_ROOT to its
isolated test directory. Complete rerun confirmed PASS for:
- disposable source+SQLite restoration and rescue history;
- mode-0600 SQLite temp, cleanup of partial gzip data;
- corrupt/incomplete source and SQLite archive rejection;
- Telegram PHOTO and pending graphic media pre-stop refusal;
- PHOTO identity change after simulated writer stop.

No actual production action was invoked. New exact-head GitHub CI
must pass before source-only development acceptance is complete.
