# Issue #340 — stable B/W lock inodes across staged source update and rollback

**QA-only proof, not a production updater or release authorization.**

This stage is stacked on draft PR #364 (B/W shutdown) and
indirectly on draft PR #363 (opt-in in-container writer fence).
None of these branches is merged or deployed in LXC110.

## Why lock pathname identity is critical

An earlier *negative* real-kernel experiment in draft PR #362
showed that when an updater replaces a **locked** B or W
pathname inode, existing writers retain their locks on the
**old** inode, while new owners can acquire incompatible
locks on the new inode. Holding an advisory `flock` on
a pathname name, without stable inode identity, therefore
does not prove single-writer admission.

This stage verifies the positive counterpart: keep
root-owned stable lock files in a separate, independent
`owner` directory and atomically change only the
`current` symlink pointing to synthetic source releases.

## Actual isolated filesystem replay

In exact LXC114 `shelly-docker-reboot-qa`, with only
`lo`, original QA signed writer stopped and restart=no,
and original systemd QA service/timer disabled, the test
creates a private `/var/tmp/svt-qa-owner-release-*`
0700 scratch folder with:

- `owner/` root UID0, group10001, mode0750;
  files `owner/B` and `owner/W` root UID0,
  group10001, mode0640, one link each and distinct inodes.
- `releases/v1/version.txt` and
  `releases/v2/version.txt`, both **synthetic**.
- `current` symlink, atomically switched using
  root-local `os.replace` **without ever unlinking,
  renaming or replacing B or W**.

A synthetic writer first acquires B SH then W EX, and
independent probes confirm a maintenance B EX and second
writer W EX are denied. While maintenance acquires B EX,
a writer B SH is denied and the active release is atomically
switched `v1 -> v2`. The code compares the actual
`st_dev`, `st_ino`, owner/group and file mode of both
B/W files against the original recorded baseline. Under
another B EX acquisition, the source is rolled back
`v2 -> v1` and B/W identities still match.

**Real offline run result: PASS**; `v1 -> v2 -> v1`,
B/W device/inode identity unchanged throughout. No
production code, SQLite, database, Docker runtime,
service, updater or Telegram is accessed for mutation.

## Test suites

- **17/17** new default-inert regression tests PASS
  locally under both root and UID65534. Tests verify
  actual `flock` B/W contention, atomic activation and
  rollback, unchanged inode/modes, fail-closed mismatched
  inode, symbolic links, hardlinks, group-writable files,
  invalid target and cleanup.
- Combined with stacked candidate QA:
  **75/75** tests PASS under both root and UID65534.
- Ruff `E,F,I,UP,SIM`, PEP8, all new functions <=40
  lines and Python lines <=79 chars PASS.
- No external network is available in QA. The script
  is inert by default and requires the exact offline
  `--run-disposable --confirm` token and sibling
  fixed-host QA guard. Unit tests replace only synthetic
  expected UID/GID constants to run unprivileged;
  real isolated QA still demands UID0/GID10001.

### Reproduce

```bash
python3 scripts/disposable-owner-release-rollback-qa.py --help
python3 scripts/disposable-owner-release-rollback-qa.py
python3 -W error::ResourceWarning -m unittest discover \
  -s tests -p test_disposable_owner_release_rollback_qa.py -v

# Exact offline LXC114 only:
python3 scripts/disposable-owner-release-rollback-qa.py \
  --run-disposable \
  --confirm OFFLINE_ROOT_OWNER_RELEASE_ROLLBACK_QA_ONLY
```

## Mandatory real release gates — still NO-GO

1. This test swaps **synthetic source symlinks**, NOT a
   real signed OCI image, active Compose container, exact
   updater archive, installed `apply-update.sh`,
   rollback script or emergency-restore service.
2. Production installed source v0.5.15-hw still differs
   from running immutable signed OCI v0.5.21-hw.
   Signed release verification and full current image
   provenance remain mandatory.
3. The actual Docker auto-restart policy is still
   `unless-stopped` and independent systemd/Compose,
   installer, updater, rollback, emergency restore and
   manual writers are NOT globally bound to the B/W
   owner protocol. A separate actual isolated QA test
   found that a synthetic in-container B/W gate prevents
   SQLite writes during Docker daemon auto-restart
   under host B EX. That does not prove real owner
   coverage.
4. Actual Telegram long-poll plus concurrent SQLite
   transaction shutdown requires end-to-end acceptance.
   Stacked PR #364 proves real source method ordering
   with synthetic worker and offline full app SIGTERM
   but does not use real Telegram API.
5. No independently restorable whole CURRENT signed
   source+SQLite WAL A/B on physically separate storage
   is configured; production Proxmox has one physical
   NVMe and LXC110/LXC114 share the same host. Real
   abrupt physical power-loss and durable recovery
   remain untested.
6. Independent source/security review and **explicit
   Owner Approval** are required before installing or
   enabling any new B/W fence in production. Public
   pinned Telegram TEXT-to-PHOTO is separately NO-GO.

**NO-GO for production deployment, app restart, image,
SQLite, lock policy, updater/rollback or Telegram changes.**
This is an isolated acceptance contract only.

Related: #336, #340, draft PRs #358–#364.
