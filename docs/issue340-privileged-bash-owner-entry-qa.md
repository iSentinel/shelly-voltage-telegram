# Issue #340: privileged Bash owner entrypoint (QA ONLY)

**Status: Draft, NO_GO for deployment.**

## Attack reproduced

The earlier `#!/usr/bin/env bash` entries for `apply-update.sh`
and `rollback.sh` admitted two separate pre-script attacks when
an upstream process supplied untrusted environment:

- `BASH_ENV` gets sourced **before** any Bash `set -e` or owner
  preflight if a noninteractive ordinary Bash interpreter runs.
- `/usr/bin/env bash` resolves `bash` from inherited `PATH`.

An LXC114 isolated scratch demonstration created only local marker
files: plain `/bin/bash script.sh` sourced synthetic `BASH_ENV`,
while `/bin/bash -p script.sh` did not. Invoking a protected
shebang through forced unprotected `/bin/bash script.sh` **still**
sourced it. This last caller limitation is an explicit release risk.

## Candidate change

- Direct owner scripts `scripts/apply-update.sh` and
  `scripts/rollback.sh` use `#!/bin/bash -p`. On Linux the
  kernel invokes root-controlled absolute Bash in privileged mode,
  so Bash startup does not evaluate `BASH_ENV`, exported Bash
  functions or inherited `SHELLOPTS`.
- Installed Compose unit's **recovery** `ExecStartPre` explicitly
  invokes `/bin/bash -p .../apply-update.sh --recover` rather than
  bypassing the protected shebang via `/bin/bash script.sh`.
- The candidate does **not** change `ExecStart` / `ExecReload`
  yet, and it does not pretend that old manual / installer /
  remote-shell callers are globally frozen.
- No rollback, update, signature, owner lock, SQLite or approval
  condition was weakened. Pending recovery still **fails closed**.
  The monitoring service could remain unavailable after interrupted
  updating. Approved boot continuity is a separate release gate.

## Actual LXC114 isolated acceptance

- New 7 test cases PASS in root mode (one expected skip), and
  7 PASS as UID65534/nobody (two expected root-only skips).
- Direct script `--help` succeeds and cannot source hostile
  `BASH_ENV` or execute a fake PATH `bash`. No-pending root
  `--recover` is inert. A forced plain `/bin/bash script.sh`
  test explicitly demonstrates the remaining caller risk.
- Real transient `systemd-run --wait --collect` Type=oneshot
  services with hostile `Environment=BASH_ENV=...` executed
  candidate `ExecStartPre=/bin/bash -p ... --recover`.
  No-pending: synthetic `ExecStart` succeeded, no poison marker.
  Pending: `ExecStartPre` refused, synthetic `ExecStart`
  did not run, pending marker retained, no poison marker.
- Both test service units collected, failure reset and scratch
  removed. No real Docker, Telegram, SQLite or Shelly mutations.
  This is not a production reboot or physical blackout test.
- Pinned Ruff 0.14.2 format, E/F/I/UP/SIM, PEP8 79 columns,
  function <=40 source lines, Bash syntax PASS.

## Still open

Full Bash/environment hardening must account for explicit
`/bin/bash` callers, systemd `ExecStart` and `ExecReload`,
install/update/rollback/manual/emergency paths and permission-
verified script/interpreter ancestry. Ownership of every
old Docker/systemd/manual writer must be frozen under stable
G/B/W/A before migration. Final *current* signed OCI and source,
stopped-writer WAL-complete twin backups independently restored,
real Telegram long-poll shutdown, physical power-loss acceptance,
security review and explicit Owner Approval including single NVMe
total-loss risk remain **NO_GO**. Public pinned PHOTO is separate
NO_GO. Production LXC110 is unchanged.
