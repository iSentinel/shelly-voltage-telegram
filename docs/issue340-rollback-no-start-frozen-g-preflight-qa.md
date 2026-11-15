# Issue #340 — rollback refuses automatic restart without frozen owners

**QA-only stacked Draft on PR #383. Do not deploy in isolation.**

The existing rollback.sh can call `compose_cmd` directly and
`compose_cmd up -d --build` on a legacy rollback. The old
OCI ignores G/B/W admission; merely wrapping the Compose CLI
in a shell `flock` cannot fence it while detached Docker
runs or when Docker autonomously restarts it.

This candidate adds `require_frozen_rollback_owner` to
the original rollback.sh, invoked **immediately after**
argument parsing and root validation, **before** any
snapshot selection, source validation, Docker stop,
rescue backup, source extraction, SQLite WAL/SHM deletion
or database replacement.

For any actual (non-dry-run) rollback:

1. Require explicit `--no-start`. No automatic
   `docker compose up -d` or `--build` after restore.
2. Require an explicit `SVT_MAINTENANCE_G_FD=N`
   inherited from the external maintenance owner.
3. Run `legacy-rollback-frozen-owner-preflight.py`
   from the original script directory using
   `--check --gate-dir --inherited-g-fd`.
4. This read-only check verifies the inherited root-owned
   G EX by Linux fdinfo and exact inode identity;
   observes the known old Docker container stopped
   with restart **no** and unchanged restart counter;
   requires known Compose/emergency systemd units
   inactive and masked.
5. Failure returns nonzero **before** touching source,
   SQLite, rescue backups or Docker. CLI `--help`
   and `--dry-run` retain inert inspect-only behavior.

The externally held G lock must remain held across the
entire restoration. This stage does not create the
external freeze, validate all manual/updater/installer
actors, deploy the gated signed image, perform data
migration or restart monitoring. Never interpret the
read-only check as all-owner freeze proof or Owner
Approval.

## Actual offline LXC114 shell QA

The candidate original Bash script was executed only
with **invented nonexistent application and SQLite
paths**. All `docker` and `systemctl` calls were
intercepted by private fake scripts; no real Docker,
systemd, production SQLite or Telegram was touched.

- Non-`--no-start` actual restore: exit5 before
  any Docker or systemd call.
- `--no-start` without inherited G FD: exit5,
  no Docker calls.
- Real kernel G EX passed from the external QA
  owner into Bash and its Python helper: narrow
  freeze check passed using fake inactive/masked
  systemd and stopped/restart=no Docker.
  Rollback then **safely refused** the missing QA
  application target; no SQLite or Compose mutation.
- Fake Docker stopped with `unless-stopped`:
  refused before target processing.
- Fake systemd active: refused before Docker inspect.
- Shell parses with `bash -n`; no file leftovers.

**8 new shell tests PASS under root**.
Cumulative **375/375 strict unit tests PASS** under
root; **375/375 under UID65534** with **5 expected
skips** for root-only shell mutation-path integration.
Ruff format/E/F/I/UP/SIM PASS, new functions<=40
lines, source width<=79. GitHub CI validates the
repository-wide suite; it does not run the real
root-only shell test paths unless the runner is root.

```bash
bash scripts/rollback.sh --help
bash -n scripts/rollback.sh
python3 -W error::ResourceWarning -m unittest discover \
  -s tests -p test_rollback_frozen_shell_qa.py -v
```

Do NOT try an actual rollback just to test the gate:
that would require a complete, independently verified
global old-writer freeze, a valid signed snapshot,
an approved current local backup and a controlled
release session.

## IMPORTANT remaining bypasses

- The external owner freeze/provisioning tool does not
  yet exist for production: the current Docker container
  is live `restart=unless-stopped`; Compose systemd
  is enabled; G does not exist on production.
- Rollback's source extraction can replace scripts,
  but the externally held G inode must remain outside
  the source tree and stable.
- `apply-update.sh --recover`, installer, watchdog,
  emergency restore, daemon restart, Docker manual
  starts and external scripts require separate coverage.
- Neither this early check nor a stopped writer from
  one instant proves all those paths remain frozen
  throughout replacement and signed-image handoff.
- A complete CURRENT WAL-consistent local two-copy
  cutover must follow full old-writer quiescence.
  Earlier real preparation copies at 72,690 rows
  are already older than live history and must
  not silently replace new measurements.
- One physical NVMe per user decision means total
  disk failure can destroy every local copy;
  explicit Owner Approval must acknowledge that risk.
- Real Telegram longpoll shutdown, power-loss QA,
  signed gated image validation and safe rollback
  acceptance remain. Pinned PHOTO remains NO_GO.

**Production release NO_GO. Do not merge/deploy a partial
guard that could strand live recovery.** No live services,
SQLite, Docker restart policy or Telegram were touched.
