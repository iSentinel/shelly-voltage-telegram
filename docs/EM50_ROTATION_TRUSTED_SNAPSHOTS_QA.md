# Rotate only verified recovery snapshots

## RED: a corrupt snapshot displaced a good one

Snapshot rotation previously counted any timestamp-shaped folder containing
source.tar.gz, a sidecar, manifest.env and one SQLite .gz file as
a complete upgrade backup. A corrupt-but-complete-looking folder could
therefore be counted among the five retained copies and trigger removal
of a legitimate older recovery point.

A disposable fixture created six valid source+SQLite snapshots plus a
newer folder with syntactically correct SHA256 sidecars but an invalid
gzip source archive. Before the fix, only four working snapshots
survived where five were required.

## GREEN: count only verified pairs

Before retaining a folder in the rotation set, the installer now
requires exactly one SQLite archive and verifies both archive/sidecar
pairs are ordinary single-linked files. SHA256 sidecars must match
the *exact selected filename* and each gzip stream must pass integrity
validation. The source TAR must list successfully; the SQLite backup
must pass the independent SQLite archive validator.

Unknown, incomplete and corrupt folders are intentionally not
counted for retention and are never deleted by the rotation path.
Healthy completed pairs remain eligible for the five-copy limit.
The production snapshot root is never inspected by fixture tests.

## Deterministic YASNO QA

The isolated full-suite run exposed an unrelated existing YASNO test
that combined a fixed 2026-11-08 fixture with the real system clock
and failed the expected unpublished-schedule assertion on 2026-11-09.
Its clock and mocked provider capture are now pinned to the same
fixture instant. No runtime YASNO behavior was changed.

## Independent verification

- RED: 4 valid backups kept instead of five.
- GREEN: all six snapshot-focused tests PASS.
- YASNO cache tests 11/11 PASS with deterministic clock.
- Isolated LXC Python 3.13: 1307/1307 PASS on the PR #280 sibling.
- Ruff 0.14.2, quality gate, Bash syntax and four installer dry-runs PASS.
- Full combined PR #291 integration must be independently rerun.

Draft source-only change on the separate #280 snapshot-rotation branch.
It must be reviewed alongside the stacked #291 branch, but neither
may be deployed without independent human source review, a fully
quiesced complete-history SQLite/source snapshot, protected YASNO
account/reader, Telegram visual signoff and signed rollout/rollback.
No live Docker/SQLite, Telegram or Shelly mutation occurred.
