# Issue #340 — opt-in Shelly writer B/W fence candidate

**Review-only source candidate. Not installed in production. Never
enable for LXC110 without the remaining owner-approved release gates.**

## Goal and exact integration points

The current `app.py` constructs `Monitor(cfg)`, which constructs
`Storage`, and subsequently opens SQLite connections and WAL.
We place the lifetime lease **before** the monitor exists:

```python
with writer_fence(os.getenv("SHELLY_WRITER_FENCE", "")):
    install_signals()
    Monitor(cfg).run()
```

Every `Storage.connect()` now calls
`assert_sqlite_writer_fenced()` immediately before
`sqlite3.connect(...)`. When a required lease is active, this
revalidates its directory and B/W lock inode identities, root
ownership, expected group, mode and link count on each opening.

The module is **inert by default**: absent or `off`
`SHELLY_WRITER_FENCE` preserves the current legacy runtime.
Any unexpected mode fails closed. **An opt-in source flag alone
does not create production fencing**: the release must explicitly
configure `required` and all host/daemon owners must cooperate.

## Lock protocol

Only `SHELLY_WRITER_FENCE=required` enables the candidate.
It opens **pre-existing** files at the fixed container directory
`/run/shelly-owner`:

| Path | Required ownership and permissions |
| --- | --- |
| `/run/shelly-owner` | directory, UID0, GID = app effective GID (10001), mode 0750 |
| `/run/shelly-owner/B` | regular file, UID0:GID10001, mode0640, nlink1 |
| `/run/shelly-owner/W` | regular file, UID0:GID10001, mode0640, nlink1 |

No lock files or directories are created or overwritten by the
writer. The directory is opened with `O_DIRECTORY|O_NOFOLLOW`;
files are opened read-only with `O_NOFOLLOW|O_CLOEXEC`,
using `dir_fd` to avoid pathname traversal, then checked
with `fstat`. The process acquires **B SH** before **W EX**,
nonblocking. A conflict, missing path, symlink, unsafe
owner/mode, hardlink or inode replacement prevents writer
startup. Both file descriptions remain open across
`Monitor.run()` and close on exit; Linux releases the locks
if the actual writer process receives SIGKILL.

Each `Storage.connect()` checks that B/W pathname inodes
and the directory still match open file descriptors and that
metadata still satisfies required ownership and modes.
A changed runtime mode cannot silently downgrade an
already active lease.

**This revalidation is not a continuously monitored, tamper-proof
lock guarantee**: a privileged actor replacing inodes between
SQLite opens can still create a dangerous interval. It also
does not prevent a pending SQLite transaction already using an
open connection from continuing after an inode swap. Stable
root-owned host inodes and a fully coordinated host lifecycle
remain mandatory external release requirements.

## QA acceptance (LXC114 only)

All work took place on `shelly-docker-reboot-qa`, a
loopback-only isolated LXC114 sharing the **same physical**
Proxmox host as production. Original QA signed writer was
stopped (`restart=no`), its service/timer were disabled.
No production data, Telegram credentials, or network entered
the experiment.

- **39/39 unit/integration tests PASS** on LXC114.
- **39/39 tests PASS as UID 65534 (nobody)** using copied
  synthetic fixture ownership; production trusted UID remains 0.
- `app.py` was copied from the current GitHub main blob SHA
  `3b42233a0ad813f9a83f3a41e98e75785b9a3138`,
  patched at exactly 3 source locations and compiled.
  Executable AST tests extract the actual
  `main()` and `Storage.connect()` without running the
  rest of the hardware/Telegram application.
- An **actual disposable Docker container** with cached fixed
  QA image ID, UID:GID10001:10001, `network=none`,
  `restart=no`, read-only rootfs, dropped capabilities,
  disabled new privileges and only invented B/W, module,
  SQLite and marker bind mounts ran the *candidate module*.
  Host B EX blocked startup **before synthetic SQLite**,
  one writer acquired B SH/W EX and inserted invented SQLite
  history, a second container writer was denied W EX,
  and SIGKILL of the labelled first container released
  host-visible B/W locks. Disposable containers, SQLite
  files and scratch were removed.
- Ruff 0.14.2 formatting and E/F/I/UP checks PASS on
  the patched source, and strict unit tests PASS.
- No real Shelly polling, Telegram API, production
  image/source deployment or production SQLite writes.

### Reproduction

```bash
python3 -m unittest discover -s tests -p 'test_writer_fence*.py' -v
python3 scripts/disposable-writer-fence-container-qa.py --help
python3 scripts/disposable-writer-fence-container-qa.py

# Exact networkless LXC114 only, original QA writer stopped:
python3 scripts/disposable-writer-fence-container-qa.py \
  --run-disposable \
  --confirm OFFLINE_OPTIN_WRITER_FENCE_DOCKER_QA_ONLY
```

## Explicit production NO-GO gates

1. **All owners:** Systemd ExecStartPre/Start/Reload, Docker daemon
   autorestart, updater/recover, installer, rollback, emergency
   restore and manual Compose must be fenced. This candidate
   modifies none of those sources or restart policies.
2. **Stable host inode identity:** root-managed B/W paths must
   be outside replaceable source and backup trees, never
   unlinked/recreated, and independently checked under
   actual systemd/Compose/rollback. The existing emergency
   helper can re-enable `restart=unless-stopped`, an independent
   bypass when not coordinated.
3. **Full lifecycle recovery:** maintenance admission B EX then
   update A EX, actual writer stop and WAL quiescence, durable
   noncircular handoff, no boot/restart race, parent crash and
   physical power-loss recovery must be independently proven.
4. **Exact signed source plus SQLite A/B:** current host source
   v0.5.15-hw differs from the real signed OCI runtime v0.5.21-hw.
   No real whole CURRENT SQLite+WAL A/B restore has been
   completed on separate physical failure domains. Proxmox
   currently has one WD_BLACK SN770 NVMe; local and local-lvm
   are not independent, and nightly backup archives are stale.
5. **Explicit Owner Approval** and independent review are required
   before modifying LXC110 production or merging any updater,
   lock ownership or release changes. Telegram pinned
   TEXT-to-PHOTO acceptance is separately NO-GO.

**Do not set `SHELLY_WRITER_FENCE=required` or add mounts on
production now.** Without the stable host files and full
cross-owner protocol, fail-closed startup may take monitoring
offline and the incomplete rollout may create a writer race.

Related: #336, #340, #353–#355, #358–#362.
