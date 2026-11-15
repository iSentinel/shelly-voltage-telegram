# Issue #340 — inherited maintenance G EX preflight for rollback

**QA-only stacked Draft on #382. Production release remains NO_GO.**

Current live rollback.sh can call `compose_cmd` directly, bypassing
the newly guarded `start_compose_main`. Current old OCI ignores
new B/W/G locks, and Docker `restart=unless-stopped` can revive a
writer after the nominal stop. A safe final SQLite cutover needs an
**externally orchestrated owner freeze**, not merely a lock around
a detached Compose CLI.

This stage introduces default-inert
`scripts/legacy-rollback-frozen-owner-preflight.py`.
Nothing is called implicitly by the *existing* production rollback.
Explicit `--check --gate-dir PATH --inherited-g-fd N` performs
**read-only** checks and exits nonzero on missing requirements.

## Narrow verified prerequisites

1. Require root and a stable root-owned 0700 gate directory with
   root-owned regular one-hardlink G lock mode0640.
2. Require a caller **actually inherited the same locked G inode**
   on the indicated open file descriptor. Compare its st_dev/st_ino
   with the trusted current path, then read Linux
   `/proc/self/fdinfo/N` for `FLOCK ADVISORY WRITE`.
   A mere same-inode descriptor that was opened independently,
   an empty/stale pathname or unlocked FD is rejected.
   No additional G lock is acquired or upgraded by the checker.
3. Using read-only Docker inspect, require *two* stable observations
   that the known `shelly-voltage-bot` is stopped, that its
   restart policy is **no** (not `unless-stopped`), and that
   RestartCount is unchanged.
4. Require known `shelly-voltage-compose.service` and
   `shelly-graphic-emergency-boot.service` to report
   `ActiveState=inactive` and `UnitFileState=masked` or
   `masked-runtime`. A merely disabled but unmasked unit
   is insufficient.
5. The narrow positive report is named
   `KNOWN_OWNER_PREREQUISITES_OBSERVED`. It explicitly includes
   `all_manual_install_update_rollback_owners_proven=false`,
   `final_cutover_authorized=false` and
   `release_decision=NO_GO`.

This is **not** an exhaustive process/owner inventory:
manual `docker` commands, external scripts/timers,
updater rollback, Compose reload paths, and daemon
auto-restart are not proven globally safe by these two
observations. Fencing must remain effective for the
**entire** SQLite restore, final local WAL snapshot, and
signed new writer handoff, not only during this preflight.
The caller holding G EX must retain the same open file
description until after the full operation.

## Real isolated evidence, LXC114 only

Tested on loopback-only guest with a private invented G inode,
real Linux `flock`, and fake `docker`/`systemctl`
binaries (no live production commands or service writes).

- Inherited exact held G EX -> narrow positive preflight,
  still `release_decision=NO_GO`.
- A **separately opened** descriptor for the very same
  G pathname while parent holds G EX -> refusal.
- A matching descriptor without held G EX -> refusal.
- The fake Docker/systemd output was *not* interpreted
  as real service quiescence evidence.

**21 new unit tests PASS**, checking fdinfo locks, inode swap,
untrusted G paths and modes, Docker active/restarting
status and restart-count race, masked versus enabled
systemd, default-inert CLI, fail-closed errors and
the explicit NO_GO outcome.

**367/367 cumulative strict QA tests PASS** as root and
UID65534. Ruff format/E/F/I/UP/SIM, functions <=40 lines,
source width <=79 and strict ResourceWarning checks PASS.
There are no leftover disposable QA directories.

### Commands

```bash
python3 scripts/legacy-rollback-frozen-owner-preflight.py --help
python3 scripts/legacy-rollback-frozen-owner-preflight.py
python3 -W error::ResourceWarning -m unittest discover \
  -s tests -p test_legacy_rollback_frozen_owner_preflight.py -v
```

Do not invoke `--check` on production until the broader
external freeze orchestration, trusted G inode and
lifetime-held inherited G EX are in place. **Do not**
use the preflight as permission to restart or restore
any real SQLite.

## Remaining release gates

The user will not add a second physical backup medium.
Existing two real preparation copies contain 72,690
measurements each but are **not a final cutover**;
live SQLite had 73,908 rows at the 21:58 Kyiv checkpoint.
The full current signed-source WAL-complete local two-copy
cutover, actual all-owner old freeze, noncircular
G EX→B EX→A EX maintenance, real Telegram longpoll
shutdown, isolated power-loss QA, rollback to signed
G-aware OCI and explicit Owner Approval are still pending.
Owner Approval must separately acknowledge total failure
of the only NVMe can destroy all local history.
Pinned Telegram PHOTO remains independently NO_GO.

No production Docker restart policy, SQLite, timers,
systemd, signed image, backups or Telegram were changed.
**Do not merge or deploy based solely on green CI.**
