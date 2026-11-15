# Issue #340 — updater systemd and internal Bash handoff (QA ONLY)

**Draft; production NO_GO.**

Read-only audit of immutable upstream PR392 discovered two surviving
unsafe explicit Bash interpreter paths despite the protected script
shebangs and Compose service:
- `shelly-voltage-updater-apply@.service` still used ordinary
  `ExecStart=/bin/bash .../apply-update.sh --version %i`.
- `start_stack()` inside the updater still ran
  `/bin/bash "$ROOT/scripts/start-compose.sh"`.

Both could load hostile `BASH_ENV` before the protected target's
shebang or G owner checks. This candidate changes only those two
callers to `/bin/bash -p`. No release authority, Docker start,
WAL recovery, current SQLite, signature or policy is changed.

Actual Gateway Proxmox LXC114 isolated QA:
- 4/4 new tests PASS root and UID65534.
- A private source of candidate `apply-update.sh` and a harmless
  synthetic child `start-compose.sh` prove the inner handoff avoids
  `BASH_ENV` and produces its expected synthetic result.
- Fixed `systemd` versioned apply `ExecStart` and inert `--help`
  entrypoints are verified.
- An explicit unsafe plain `/bin/bash script.sh` still imports the
  fake startup code: negative control confirms scope.
- Bash syntax, pinned Ruff0.14.2 E/F/I/UP/SIM, format, 79 columns
  and functions <=40 lines verified. All scratch removed.
- No real Docker, Telegram, SQLite, Shelly or production systemd
  unit was touched.

Not solved: manual/install/backup/recovery helpers and many other
explicit Bash/Python launches remain to be reviewed; signed current
OCI and WAL-current twin backups + independent restores, full old
owner global G/B/W/A freeze, verified interrupted-boot continuity,
real Telegram teardown, physical power-loss QA, independent review
and explicit Owner Approval acknowledging possible complete single
NVMe loss remain release NO_GO. Pinned public PHOTO is separate
NO_GO. Production LXC110 remains on the current healthy image.
