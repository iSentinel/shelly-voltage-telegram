# Issue #340: legacy updater and boot recovery fail-closed (QA only)

**Status: DRAFT, NO_GO.** Stacked on PR #384 and all preceding
unmerged owner-fence safety changes. Not safe for independent deployment.

## Why this stage exists

The installed legacy `apply-update.sh` can be invoked by an update unit,
manually, or as the Compose boot `ExecStartPre --recover`. It has its own
old A-only updater lock, but it does not hold the proposed global G/B/W
owner protocol while stopping Docker or replacing SQLite/WAL. An old
autonomous Docker writer could restart during an unguarded handoff.

A successful narrow inherited-G check does **not** prove that all
manual, systemd, installer, updater and emergency owners are frozen.

## Proposed source-only fuse

- A boot with no `pending-update` returns zero without creating state,
  starting Docker or touching SQLite.
- A pending update requires an inherited real G EX proof and the existing
  frozen-owner preflight, but still exits 5 with NO_GO before any restore.
- A real `--version` update refuses before stateful `requirements` and
  before image download/pull, Docker stop or SQLite access. Even a valid
  inherited-G narrow check never grants permission to proceed.
- A symlinked state directory or pending marker is rejected.
- `--help` remains inert. Existing `--dry-run` behavior remains as-is;
  it is **not** a substitute for fully read-only acceptance.
- This is a deliberate stopgap: it blocks automatic recovery when a
  pending marker exists, so boot readiness may be unavailable. Do not
  merge/install until an approved boot-continuity policy covers this case.

## Isolated acceptance evidence (2026-11-15)

Gateway SSH / Proxmox, LXC114, network interface **lo only**.
The candidates were streamed into a private temporary QA directory;
there were no real Docker, systemd, Shelly, Telegram or live SQLite calls.

- `bash -n`: PASS.
- `python3 -m unittest discover -s tests -p
  'test_legacy_updater_owner_fuse_qa.py' -v`: 11/11 PASS as root
  (1 correct nonroot-only skip).
- The same 11 tests PASS as UID65534/nobody (6 correct root-only skips).
- Real Linux `flock` G EX + mocked inactive/masked systemd and stopped
  `restart=no` Docker still returned NO_GO.
- Active legacy systemd, missing G, pending marker symlink and nonroot
  recovery attempts all fail closed. No QA scratch remains.

This is targeted smoke acceptance, **not** full stacked CI, signed image
verification, physical blackout acceptance, or global all-owner fencing.

## Remaining release gates

Externally freeze and join every old writer/start actor; implement and
review globally noncircular G EX -> B EX -> A EX plus startup G SH/B SH/W EX,
and continuously stable root-owned lock inodes. Make the stopped-writer
CURRENT signed source/OCI and WAL-complete SQLite twin backup/restore
verifiable and fresh at cutover. Validate Telegram long-poll teardown,
power-loss and safe boot continuity in disposable QA. Only then request
explicit Owner Approval including total data-loss risk from a single NVMe.

**Production LXC110, Telegram, Docker restart policy and SQLite are unchanged.
Public pinned PHOTO migration remains separately NO_GO.**

## Real transient systemd boot prestart acceptance (LXC114)

2026-11-15 Gateway execution in the offline loopback-only QA guest:
two distinct `systemd-run --wait --collect` transient Type=oneshot
services each ran the **candidate** `apply-update.sh --recover` via an
actual `ExecStartPre`; each `ExecStart` only touched a private QA marker.

- **No pending-update:** prestart SUCCESS (unit result success,
  main exited 0). The synthetic main marker exists: PASS.
- **Pending-update present:** prestart failed (unit job exit 1),
  synthetic main marker absent, pending marker still exists: PASS
  fail-closed and **confirmed boot-availability risk**.
- Both units were collected, transient failures reset, private scratch
  removed. No real image, Docker daemon, SQLite, Telegram or service
  settings were modified. This was not a physical reboot.

Do **not** infer that the production Compose service can start after
this candidate is deployed: the separate gated-only Compose entrypoint
and any approved current signed OCI safe fallback still need full review.

