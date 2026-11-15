# Issue #340 — isolated Python for Compose admission (QA ONLY)

**Draft. Production NO_GO.**

## Scope

The existing `start-compose.sh` performs a fail-closed G/B/W
and pinned-OCI permit check through `legacy-compose-start-gate.py`.
Previously it invoked ordinary `python3`, allowing untrusted
`PYTHONPATH` and `PYTHONHOME` to interfere with imports before
permit verification. Protected `/bin/bash -p` alone does not
sanitize Python's module search.

The candidate changes precisely that admission invocation to
`/usr/bin/python3 -I`. No additional Docker calls, start
fallbacks, SQLite restores or release approvals are introduced.
The fixed-root Python interpreter itself needs independent
root-owned executable provenance review.

## Isolated evidence through Gateway

Actual Proxmox LXC114; no real Shelly, Telegram, Docker or production
SQLite operations. A synthetic pinned override and deliberately missing
G lock ensure Compose fails closed **before** any Docker command.
A private fake `argparse.py` writes a test marker if imported.

- Older unisolated private copy: fake `argparse` import executed.
- Candidate: missing G returns `COMPOSE_ADMISSION_DENIED`, no
  poison marker and no SQLite created even with fake PYTHONPATH and
  bogus PYTHONHOME.
- Synthetic `--help` remains inert.
- **5/5 new tests PASS root; 5/5 PASS UID65534.**
- Pinned Ruff 0.14.2 format/E/F/I/UP/SIM, PEP8 79-column maximum,
  functions <=40 source lines, Bash syntax PASS.
- Private scratch automatically deleted.

## NO_GO remains

This stage covers only the Compose permit Python invocation; other
Python entrypoints, manual installers, emergency starts, systemd
and shell environment sanitization remain independent work.
The installed old image can auto-restart; a global old-owner
G/B/W/A freeze is not implemented. Before any promotion require
approved pending-boot continuity, current signed OCI/source and
two WAL-complete restorable local backups, real Telegram long-poll
shutdown, physical power-loss QA, security review, and explicit
Owner Approval acknowledging that one failed NVMe can destroy
all local copies. Pinned public PHOTO migration remains NO_GO.
LXC110 production runtime and SQLite are unchanged.
