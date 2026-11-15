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

Actual isolated LXC114 results: **3/3 supervisor SIGKILL scenarios PASS**
and repeat PASS. All private scratch directories removed.
Original signed QA writer remains stopped with restart=no and original
QA units disabled. LXC114 has only `lo`; no network interface is
usable by the experiment.

**52/52** combined (24 inherited-B + 12 writer-admission) `ResourceWarning`-strict unit tests PASS,
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

## Additional single-writer admission evidence — 2026-11-15

A **second actual offline Unix flock experiment** in the same
networkless LXC114 addresses another logical issue in a naive
B SH-based design. B SH is a **shared** lock: any number of
cooperating writer launchers could simultaneously acquire it,
even though it blocks maintenance B EX.

Two independent root-private synthetic lock inodes were created:
**B (maintenance)** and **W (single-writer)**, both mode0600.
Synthetic writers are only fixed `sleep 3` commands using
`/usr/bin/flock --no-fork` so lock ownership remains with
the exact sleeping process. There is no Docker, SQLite,
Shelly or Telegram call in these experiments.

| Real isolated case | Actual result |
| --- | --- |
| Two processes each acquire B SH alone | Both run simultaneously; **single-writer violation** |
| First process acquires B SH then W EX | Second B SH + W EX admission fails immediately |
| Maintenance B EX while B SH / W EX writer runs | Denied |
| Synthetic worker exits | Lock file descriptors close and locks release |

**2/2 actual subprocess admission cases PASS.** The companion
`scripts/disposable-single-writer-admission-qa.py` is inert by
default and requires exact offline LXC114 confirmation:

```bash
python3 scripts/disposable-single-writer-admission-qa.py --help
python3 scripts/disposable-single-writer-admission-qa.py

# EXACT OFFLINE LXC114 only, writer stopped, no external interfaces:
python3 scripts/disposable-single-writer-admission-qa.py \
  --run-disposable \
  --confirm OFFLINE_SINGLE_WRITER_ADMISSION_QA_ONLY
```

The new **12/12 default-inert unit tests PASS**, combined with
the inherited-B package for **52/52 strict unit tests PASS**
on LXC114. Ruff 0.14.2, PEP8, <=40-line functions and <=79-char
source lines pass. All scratch directories cleaned.

**Design implication:** a production protocol must separately
ensure *writer exclusivity* and *maintenance exclusion*, not
mistake shared B for a single-writer election. A synthetic
lock order **B SH -> W EX** for a writer and **B EX ->
maintenance A EX** for updater is an illustrative candidate
only; the actual writer-supervisor lifecycle, all Docker
and systemd start owners, noncircular ordering, daemon
restart, concurrent login, stale-writer recovery and physical
power-loss have NOT been validated. W is not installed in
production. This is **NO-GO** for automatic deployment.

## Combined B SH and W EX survival across SIGKILL

The earlier inherited B model and the single-writer admission model
were subsequently **combined in one actual orphaned-child test**.
The synthetic supervisor acquires B SH and W EX **before** launching
the writer, and both underlying file descriptors are included in
the same `pass_fds` tuple. After independently proving both
inodes are held, the supervisor receives real `SIGKILL`.

While the synthetic Unix writer remains running, a separate
maintenance B EX probe and a second writer W EX probe both fail.
After it exits, both B EX and W EX become available.

| SIGKILL scenario | B EX during live child | W EX during live child |
| --- | --- | --- |
| Parent-only B SH | ALLOWED_GAP | Not applicable |
| Inherited B SH | BLOCKED | Not applicable |
| **Inherited B SH + W EX** | **BLOCKED** | **BLOCKED** |

**3/3 actual subprocess SIGKILL scenarios PASS**, plus the
two concurrent-writer admission cases **2/2 PASS**.
The added five W lifetime regression tests bring the combined
default-inert suite to **36/36 ResourceWarning-strict unit tests
PASS**, Ruff0.14.2/PEP8 and <=40-line functions PASS on isolated
LXC114. Every private test directory is cleaned.

**Limits remain unchanged:** these are Unix processes and a fixed
sleeping synthetic child. No Docker OCI container can be assumed to
inherit B/W host descriptors. There is no real systemd supervisor,
SQLite WAL writer, signed source, daemon reboot or physical power
loss here. The tests establish an acceptance property for a future
design, **not** a deployment-ready implementation or Owner Approval.

## Actual isolated Docker in-container B/W flock acceptance

This follow-up goes beyond inherited Unix-process file descriptors.
An **actual disposable Docker container** running the exact cached
LXC114 QA image executes Python `fcntl.flock` **inside** the
container on two host-owned bind-mounted lock inodes. This is
*not* the production Shelly image or application code.

The test is restricted to exact LXC114
`shelly-docker-reboot-qa`, only `lo` network interface,
original signed QA writer stopped with restart=no, original
QA systemd owners disabled, and exact local cached immutable
image **ID**. No pull, external network, secrets, source install,
production database or Telegram calls occur.

The disposable container has `--network none`,
`--restart no`, `--read-only`, no Linux capabilities,
`no-new-privileges`, nonroot UID:GID `10001:10001`,
bounded CPU/memory/PIDs, an exact owner label and only two
private QA mounts: root-owned group-accessible B/W lock files
as readonly bind mount, plus synthetic event markers in a
private writeable directory. QA lock files are mode 0640 with
group 10001; the mount directory is mode 0750. The container
opens the lock files **O_RDONLY**; Linux `flock` operates
on the underlying open file description across the bind mount.

| Actual isolated Docker scenario | Result |
| --- | --- |
| Host holds B EX before launch | Container cannot acquire B SH; marker `DENIED_B` |
| Host releases B; first container acquires B SH and W EX | Both host B EX and host W EX denied |
| Second container tries B SH then W EX | `DENIED_W` while first writer stays active |
| First disposable container receives SIGKILL | Host can reacquire both B EX and W EX |
| Sandbox ownership and cleanup | All disposable containers and scratch removed |

**All four actual Docker lock checks PASS**, repeated successfully.
The script is inert by default and requires the exact confirmation
token on isolated LXC114:

```bash
python3 scripts/disposable-container-host-bw-flock-qa.py --help
python3 scripts/disposable-container-host-bw-flock-qa.py

# EXACT OFFLINE LXC114 ONLY:
python3 scripts/disposable-container-host-bw-flock-qa.py \
  --run-disposable \
  --confirm OFFLINE_CONTAINER_HOST_BW_LOCK_QA_ONLY
```

The new **16/16 default-inert unit tests PASS**, bringing this
PR to **52/52 strict unit tests PASS** on LXC114.
Ruff0.14.2, PEP8, function size <=40 and source line width <=79 PASS.
Cleanup requires an exact `org.svt.qa.lockowner` label and disables
restart before removing only the disposable container.

**Critical release distinction:** the experiment proves actual
host/container `flock` interoperability when the *in-container
writer process itself* participates in B SH and W EX.
It does **not** prove that the installed production Shelly
writer implements this lease, that an arbitrary Compose
or rollback path cannot bypass it, that one physical LXC
host crash preserves SQLite current history, that the
root/group permission model is approved for production,
or that a Docker daemon/host reboot cannot introduce a
new racing writer during an interrupted update.

Production design must bind the owner contract to the real
application before any SQLite handle opens; verify all
updater/installer/recovery/rollback entry points, durable
admission and noncircular B EX/A EX maintenance, signed
source/current database A/B on independent physical storage,
actual power loss and explicit Owner Approval. Until then
**production and public Telegram TEXT-to-PHOTO remain NO-GO**.
