# Issue #356: Bash status publication candidate (stacked on PR #336)

**DRAFT, REVIEW ONLY. No production installation or merge approval.**

This source-only branch starts from the exact
`qa/pending-recovery-preserve-live-20261114` head
`6a639cd9dfb9b5f680bb4b8cd2fd5ac221e0da44` (PR #336).
It does not fix unrelated global writer ownership or approved
power-loss durability gates.

## Defect reproduced before this candidate

The candidate `apply-update.sh` cleared `pending-update` before
writing the public `deployed.json`, and treated public sync failure
as a warning. An isolated forced failure returned exit 0 with
`UPDATE PASS`, private deployed version advanced, public version
missing or stale, and the pending marker already removed.

## Source change for independent review

- Publish public JSON to a mode-0644 file in the same public directory,
  fsync the file, atomically replace the old public path, then fsync
  the parent directory.
- Do not clear `pending-update` until public publication succeeds.
- After the image/health activation is complete, disable the older
  recovery-on-ERR trap for **metadata-only** errors to avoid rolling
  back valid newer SQLite history merely because public status failed.
- Return explicit nonzero exit codes: 71 for public publication
  failure, 72 for pending-marker deletion failure, 73 for failed
  durability confirmation after marker removal.
- Only print `UPDATE PASS` when public publication and pending
  clearance were reported successful.
- Preserve root-owned public mode 0644. Isolated real systemd
  `DynamicUser=yes` tests showed root-owned 0600 public status
  was not readable, whereas 0644 was.

## Reproduce safely

```bash
python3 scripts/disposable-public-status-bash-qa.py --dry-run
python3 -W error::ResourceWarning -m unittest discover \
  -s tests -p test_disposable_public_status_bash_qa.py -v

# ONLY on isolated networkless LXC114, after staging exact hashes:
python3 scripts/disposable-public-status-bash-qa.py \
  --run-disposable \
  --confirm ISOLATED_REAL_BASH_PUBLIC_STATUS_QA_ONLY
```

The disposable QA CLI requires exact hostname
`shelly-docker-reboot-qa`, no non-loopback interface, and a stopped
QA writer with Docker restart=no and the original QA systemd owners
disabled. It SHA-pins the unmodified PR #336 source, the modified
candidate Bash source, and creates only fresh private synthetic
metadata and SQLite under a mode-0700 temporary directory.

GitHub release lookup, signature verification, Docker pulls,
actual Compose starts/stops and health checks are **MOCKED**.
The candidate Bash `main`, `mark_pending`, `activate`,
`sync_public_version` and `complete_public_status` are real.

## Isolated acceptance on LXC114 (2026-11-15)

| Injected scenario | Exit | Public metadata | Pending |
| --- | ---: | --- | --- |
| Normal successful commit | 0 | New | Absent |
| Public install fails | 71 | Old | Retained |
| Public directory fsync fails | 71 | New | Retained |
| Pending unlink fails | 72 | New | Retained |
| Pending directory fsync fails | 73 | New | Absent |

All five real Bash candidate scenarios PASS their exact assertions;
synthetic SQLite 2/2 rows and quick_check preserved in every case.
The candidate never emits `UPDATE PASS` in four failure scenarios.
QA Ruff 0.14.2 and 11/11 strict Python unit tests PASS;
`bash -n` PASS. No production image, source, SQLite or Telegram
activity is performed.

## Remaining BLOCKERS: release NO_GO

1. A failed pending-directory fsync after unlink can leave
   `pending-update` absent. Exit 73 is correctly nonzero, but the
   transaction still needs an independently verified durable
   boot-time reconciler; do not infer commit from a missing marker.
2. The existing private `deployed.json` activation write also needs
   independent crash/durability review.
3. Docker `unless-stopped`, Compose boot/start/reload, manual
   updater, installer, rollback and emergency restore remain
   independent production owners. A fully reviewed noncircular
   global B/A owner lock protocol is NOT deployed.
4. Real signed OCI source parity, fresh frozen whole-CURRENT SQLite
   and signed-source A/B, independent restore, physical power loss
   and explicit owner maintenance approval are missing.
5. The public Telegram pinned TEXT-to-PHOTO transition is separate,
   not approved and must not be triggered by this candidate.

**Do not merge/deploy solely on green CI or isolated QA.**
This draft is a narrower source candidate for independent review,
not a complete fix for the full safety problem in Issue #340/#356.
