# Issue #340 — combined trusted owner + pending boot WAL QA

**Status: stacked DRAFT, never a production release authorization.**

This integration candidate composes the independent source-only owner
command-path hardening from Draft #386 onto the pending-update
fail-closed protection (#385) and repeated-boot WAL matrix (#387).
Its base is #387; #386 is a sibling and could be merged separately.
Reconcile the final merge ancestry before enabling any runtime owner.

## Why combine them

The original inherited-G owner check resolved Docker and systemd
using an inherited, potentially untrusted PATH. A bogus command could
claim a nonrunning old Docker writer and masked inactive systemd.
The inherited-G lock by itself did not authenticate inspection output.

The exact candidate `legacy-rollback-frozen-owner-preflight.py` from
#386 uses only absolute canonical, root-owned, nonlinked executables
with trusted ancestor paths and nonwritable inodes. This makes a forged
PATH fake fail rather than authorizing release.

The code still returns `NO_GO` if only narrow old owner conditions
are proven; full Docker/systemd/manual/installer/updater/emergency owner
freeze and G/B/W/A lifecycle remains unimplemented in production.

## Actual isolated Gateway acceptance

Executed in Proxmox **LXC114**, network loopback only, ephemeral
private QA tree; root and UID65534 test runs each invoked only
synthetic SQLite and fake Docker/systemd commands. Both candidate
modules came from the same GitHub QA integration branch.

- **31/31 targeted unit tests PASS as root** (3 expected skips)
- **31/31 targeted unit tests PASS as UID65534/nobody**
  (14 expected privileged-only skips)
- Bash syntax of the old updater fuse PASS
- Full tests include 30-row real SQLite WAL across repeated pending
  boot attempts, 27-row WAL preservation when frozen owner proof is
  narrowly valid, symbolic/hardlink/path permission attacks and
  root-owned absolute executable resolution
- No actual production SQLite, Docker, Shelly, Telegram or systemd
  units touched; temp QA tree cleaned automatically

These are narrow QA invariants, **not** a real powered-off reboot
of a production replica, a current signed-image fallback, or final
security review. Full GitHub CI must pass separately.

## Explicit still-open release gates

Do not approve or deploy this integration unless a reviewed final
branch includes the full old-owner freeze and G/B/W/A protocol,
approved pending-update boot continuity, signed OCI and matching
source, two fresh stop-writer WAL-consistent CURRENT local snapshots
and independent restores, actual Telegram long-poll termination,
physical power-loss QA, trusted Python/Bash environment sanitization,
independent review, and explicit Owner Approval recognizing complete
NVMe failure may destroy all telemetry and local copies.

The public pinned-photo switch is an independent NO_GO.
Production LXC110 and running image were not modified by this QA.
