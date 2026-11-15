# Issue #340 — privileged Bash for Compose start/reload (QA ONLY)

**Status: Draft, production NO_GO.**

## Scope and safety boundary

The stacked update/rollback hardening from Draft #390 protects
`ExecStartPre`, but the installed Compose unit still called
`ExecStart=/bin/bash .../start-compose.sh` and
`ExecReload=/bin/bash .../start-compose.sh`. Those explicit
interpreters bypass the script shebang and let inherited `BASH_ENV`
execute before even a fail-closed Compose gate.

This candidate sets:
- `scripts/start-compose.sh` shebang to `#!/bin/bash -p`.
- Compose `ExecStart` and `ExecReload` to `/bin/bash -p`.
- Existing `ExecStartPre=/bin/bash -p ... --recover` remains.
- The old Compose gate, exact OCI digest, Docker controls, SQLite
  and Telegram are unchanged. This candidate does **not** start
  or restart production Docker.

## Isolated QA evidence

Actual Gateway Proxmox LXC114, network loopback-only:

- Five new `unittest` cases PASS root, five PASS UID65534.
- Direct script `--help` ignores hostile synthetic `BASH_ENV`
  and fake `PATH` Bash, while `--build` remains denied before
  Docker access. A deliberately unsafe `/bin/bash script.sh`
  invocation still executes the fake startup file, proving the
  residual scope of this patch.
- Exact pinned Ruff0.14.2 format/E/F/I/UP/SIM, function <=40
  source lines, <=79 columns, Bash syntax PASS.
- An actual transient `systemd-run` unit with `Type=oneshot`,
  `RemainAfterExit=yes`, poisoned `BASH_ENV`, and only
  `start-compose.sh --help` as its synthetic main:
  both `ExecStart=/bin/bash -p` and
  `ExecReload=/bin/bash -p` succeeded without a poison marker.
  Transient unit and QA scratch cleaned after the check.

No live OCI, Docker, Shelly, Telegram or SQLite operation occurred.

## Production NO_GO

The old writer may still auto-restart; manual/inherited shell paths,
installer, updater, emergency boot, trusted script source, launch
environment and multi-process G/B/W/A owner freeze remain open.
The Compose gate also invokes other Python code whose trusted
interpreter and script provenance require independent review.
A true interrupted boot still needs an approved signed-current-image
fallback and no stale SQLite rollback. Final fresh WAL-complete
two-copy restore, real long-poll shutdown, physical power-loss
test, security review and explicit Owner Approval acknowledging
the single-NVMe loss risk are prerequisites. Public pinned PHOTO
is separately NO_GO.

**This Draft is not a deployment instruction. LXC110 is untouched.**
