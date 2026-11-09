# Upgrade snapshot rotation must preserve unrelated recovery data

## Reproduced failure

An isolated source-only snapshot test created an unrelated operator recovery
folder, then produced six valid versioned source+SQLite snapshots. The prior
rotation logic selected every immediate subdirectory by mtime and removed the
unrelated folder as the oldest entry. This was demonstrated without touching
production or any real archive.

## Fail-closed change

The installer now rotates only its own recognizable timestamp/version/random
snapshot directories that also contain a source archive, source checksum,
manifest and SQLite archive. Unrelated folders and incomplete recovery
snapshots are deliberately retained for operator review. The existing
five-valid-snapshot retention and paths with spaces remain supported.

## Acceptance

- Isolated RED reproduction: unrelated recovery directory removed before fix.
- GREEN regression: unrelated and incomplete recovery folders survive six
  captures; five complete snapshots remain.
- 5/5 focused snapshot tests PASS.
- 1306/1306 Python 3.13 tests PASS in an isolated LXC QA copy.
- Ruff 0.14.2 formatting/lint and project quality gate PASS.
- Bash syntax, Python syntax and Git diff whitespace checks PASS.

## Deployment status

This change is a draft stacked on PR #278, which is itself stacked on #277.
It does not authorize deployment. Preserve independent source review, stopped
writer verification, exact live SQLite history parity, signed rollout/rollback
approval, protected YASNO settings and real-client Telegram acceptance.
No production database, Docker service, Shelly, Telegram destination or
existing backup was modified for this test.
