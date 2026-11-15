# Issue #340 — inherited B-SH file descriptor lifetime after SIGKILL

**Default-inert QA-only test. No production Docker, database, systemd,
updater, release, Shelly device, Telegram message or credentials used.**

## Context

Read-only production owner inventory and a **real isolated Docker**
experiment in draft PR #360 proved that wrapping
`docker run -d` in a B SH `flock` protects only the startup command.
After that command exits, the Docker container may keep writing while
another maintainer acquires B EX. Docker's independent
`restart=unless-stopped` manager also ignores host advisory locks.

This new script tests a narrower kernel property with a **synthetic
Unix process**, not Docker: what happens to a B SH flock if the
supervisor is killed with `SIGKILL` while its writer child lives?

## Two independently executed real subprocess scenarios

On exact loopback-only LXC114, the script creates a private 0700
scratch tree and root-owned 0600 advisory maintenance lock.
A synthetic supervisor acquires B SH before launching a
nonnetworked Python child. No SQLite, Docker or external traffic
exists inside either synthetic role.

1. **Negative control — parent-only fd:** supervisor holds B SH;
   child is spawned without `pass_fds`. While both run, B EX is
   denied. After the supervisor is killed, the child continues
   running but an independent B EX probe succeeds. This is the
   orphaned-writer lock gap.
2. **Positive control — inherited fd:** the same pre-acquired B SH
   open-file description is explicitly passed via Python
   `subprocess.Popen(pass_fds=(fd,))` to the child before it
   starts. After `SIGKILL` of supervisor, the child is still
   running and B EX remains **blocked**. When the child exits,
   B EX becomes available.

| Condition | Parent-only B SH | Inherited open-file-description B SH |
| --- | --- | --- |
| Writer running, supervisor alive | B EX denied | B EX denied |
| Writer running, supervisor SIGKILLed | B EX **allowed (unsafe gap)** | B EX **denied** |
| Writer exited | B EX allowed | B EX allowed |

Actual isolated LXC114 results: **2/2 subprocess scenarios PASS**
and repeat PASS. All private scratch directories removed.
Original signed QA writer remains stopped with restart=no and original
QA units disabled. LXC114 has only `lo`; no network interface is
usable by the experiment.

**19/19** independent `ResourceWarning`-strict unit tests PASS,
Ruff 0.14.2/PEP8 PASS, all functions <=40 lines and Python code
lines <=79. Negative tests check exact user consent, root/hostname,
lo-only NIC, original QA writer and systemd unit state, invalid
PID/child handshake, missing state markers, lock contention and
child cleanup.

## How to reproduce

Inert on every host, by default:

```bash
python3 scripts/disposable-inherited-b-lifetime-qa.py --help
python3 scripts/disposable-inherited-b-lifetime-qa.py
python3 -W error::ResourceWarning -m unittest discover \
  -s tests -p test_disposable_inherited_b_lifetime_qa.py -v
```

For **LXC114 only**, after confirming original QA writer is
stopped with `restart=no` and QA units disabled:

```bash
python3 scripts/disposable-inherited-b-lifetime-qa.py \
  --run-disposable \
  --confirm OFFLINE_INHERITED_B_LIFETIME_QA_ONLY
```

## Critical limitations and release gates

- Docker containers **do not automatically inherit an open host
  flock file descriptor** via `docker run`. This result does not
  demonstrate that a Docker writer can be protected by this exact
  mechanism. The test is a Unix kernel/process model, not a
  systemd or container-lifecycle integration test.
- When the model's child process exits, the lock releases, but
  no actual SQLite WAL flush, image trust check, supervisor
  reboot or daemon restart is involved.
- A production owner authority must cover **every path**:
  Docker auto-restart policy, systemd ExecStartPre/Start/Reload,
  updater/recover, installer, rollback, emergency restore,
  any watchdog/timer that can launch writer. A single
  flock around `compose up -d` does not suffice.
- The pre-acquired owner lease must survive unexpected supervisor
  failures **without** keeping a stale lock forever and prevent
  maintenance until the actual writer has fully stopped and its
  current SQLite history is safe. The lock-order design must
  avoid circular B EX/A EX dependencies.
- Draft PR #361 tests two independently restored **synthetic**
  SQLite backups. Actual live SQLite history A/B on separate
  physical failure domains remains untested. Production Proxmox
  currently has one physical WD_BLACK SN770 NVMe and local-only
  backups.
- Real immutable GHCR runtime cosign verification separately
  passed read-only, but host source 0.5.15-hw remains older than
  running signed v0.5.21-hw. Exact signed-source plus CURRENT
  SQLite A/B and physical power-loss acceptance are still
  required.
- **NO-GO** for production deployment, rollback, runtime
  restart, restart-policy change or public Telegram pinned
  TEXT-to-PHOTO. Independent security review and explicit
  Owner Approval required.

Related: #335, #336, #340, #353, #354, #355, #358, #359,
#360 and #361.
