# Issues 340/422/431: close QA SQLite fixtures before CI checks

Date: 2026-11-19. State: source-only QA; no production change.

The CI workflow explicitly scans the complete unittest output for
ResourceWarning and treats one as a failed build. In the 220-test
Python3.13 source-only integration set, two warnings previously
appeared when garbage collection reached SQLite test fixtures.

Python tracemalloc located the allocations in:
- tests/test_stage_guarded_update_policy.py, create_fixture, line 142.
- The same file, test_sqlite_digest_detects_changed_photo, line 133.

The Python context manager for sqlite3.Connection handles
commit/rollback but does not necessarily close the connection.
Both fixture scopes now combine contextlib.closing() with the
database transaction context manager. Commit or rollback occurs
before the connection is explicitly closed.

Verification in isolated LXC114 (network none, UID10001, read-only
root filesystem):
- 16 focused stage/paired-rescue tests PASS under
  -X tracemalloc=5 -W error::ResourceWarning.
- 220/220 selected linked source-only QA tests PASS with -W default.
- The CI-style explicit grep for ResourceWarning returned no match.
- Ruff0.14.2 and maintainability checks PASS.
- No production database access, Docker restart, Telegram API action
  or image release occurred.

This does not establish full-suite resource safety or the exact-head
Python3.11/3.12/3.13 GitHub CI result. Those gates remain required.
