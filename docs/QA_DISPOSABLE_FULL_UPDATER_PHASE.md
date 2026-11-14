# Full candidate updater phases with stale-WAL rollback QA

**Issue #340. QA-only, review-only. NOT a real signed release install.**

## Purpose and safety

Previous PR #352 exercised candidate recover_pending, but not the
entire manual --version transaction. The installed host source
remains 0.5.15-hw while the live signed runtime is 0.5.21-hw.
Installed old-source rollback can delete current SQLite WAL/SHM
and restore an older archive, which is not yet globally fenced.

This test sources **exact SHA-256-pinned candidate PR #336
apply-update.sh**, never installed production updater. It runs
on root-only networkless loopback-only synthetic LXC114, and
copies only the synthetic QA Storage database to private
0700 temporary fixtures. The original QA writer must be stopped
with Docker policy=no and its systemd services disabled.

## What actually runs

- Real candidate main --version, snapshot, mark_pending, activate,
  rollback and recover_pending functions.
- Real backup.sh online SQLite backup + gzip + SHA-256.
- Full synthetic SQLite history (876 original rows); a deliberate
  committed 221.7 V row during the private start barrier.
- Parent B maintenance EX and child A updater EX locks using
  two private distinct inodes. Independent processes confirm
  another B SH supervisor and A EX updater are blocked.
- Candidate PR #336 read-only SQLite history guard: refuses an
  out-of-date archive before any real Docker Compose stop.
- Private file removal and lock release after each scenario.

**Mocked deliberately for offline safety:** GitHub release lookup,
signature verification, source download, image pull, readiness
probe, and every actual Compose start/stop operation. The shell
Docker mock validates the expected pull arguments and refuses
other commands; it never contacts the Docker daemon. Synthetic
start-compose observes pending and either succeeds or inserts a
new WAL row before a simulated exit77 failure.

Do not claim this test validates signatures, remote releases,
real Docker startup, atomic production metadata or power-loss.

## Actual two-case acceptance (2026-11-15)

| Scenario | Candidate exit | History retained | Pending |
|---|---|---|---|
| Full --version successful transaction | 0 | 876 / 876 | Cleared |
| New WAL row after snapshot; failed startup | 77; HISTORY_GUARD BLOCKED | 877 / 877 | Retained |

The gzip snapshot in each case has a valid SHA-256 checksum and
passes SQLite quick_check with all original 876 rows. The
synthetic live SQLite keeps any new committed row on failure.
Both B and A locks are released, and private scratch data is
deleted. The original networkless writer stays stopped.

## Reproduce

Default no-op:

    python3 scripts/disposable-full-updater-phase-qa.py --dry-run

Only after staging the SHA-256-exact PR #336 Bash candidate,
SQLite history guard and backup.sh under
/root/svt-pr336-guard on networkless LXC114 (backup named
qa-backup-only.sh):

    python3 scripts/disposable-full-updater-phase-qa.py \
      --run-disposable \
      --confirm ISOLATED_FULL_UPDATER_PHASE_QA_ONLY

-h/--help supported. Exit 0 = inert dry-run or all private QA
checks PASS; exit 2 = wrong confirmation; exit 3 = fail-closed
host/credentials-free source/integrity/runtime validation failure.
No arbitrary target paths or production credentials are used.

## Release blocker

Global production startup owners (Docker unless-stopped, Compose
ExecStartPre/ExecStart/ExecReload, updater, installer, rollback,
emergency restore) do not yet share one B/A lock protocol.
Advisory locks cannot fence owners that ignore them. Full actual
signed-source and whole-CURRENT SQLite A/B, independent recovery,
bounded boot and physical power-loss testing, and explicit owner
maintenance approval remain required. The public pinned Telegram
TEXT-to-PHOTO transition is separate and still NO_GO.
