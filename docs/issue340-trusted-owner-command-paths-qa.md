# Issue #340 — trusted owner command paths for frozen rollback

**QA-only stacked Draft on #384. Production release is NO_GO.**

The inherited G EX preflight in Draft #383 and the candidate
guarded `rollback.sh` in Draft #384 query real Docker and
systemd state. A raw `subprocess.run(["docker", ...])` or
`["systemctl", ...]` will use the caller's `PATH`,
possibly allowing an untrusted executable to assert a
fabricated "stopped, restart=no" owner state.

This stage hardens the read-only preflight before every
external command:

- Only the fixed command names `docker` and `systemctl`
  are permitted. They are resolved once via `shutil.which`
  and then executed by the **validated absolute path**,
  rather than the original relative command name.
- The resolved filename must be absolute, canonical,
  regular, executable, root-owned, one-hardlink and not
  group/world-writable. Executable aliases/symlinks,
  writable files and missing paths are rejected.
- The entire directory chain must be root-owned regular
  directories without group/world write access. Root-owned
  sticky `/tmp` or `/var/tmp` are accepted solely as
  protected parents of separately root-owned private
  subdirectories, matching the isolated CI fixtures.
  Other world/group-writable parents are rejected.
- Any failed executable validation becomes a nonzero
  owner-freeze refusal **before** it can claim the old
  Docker writer was quiesced.
- The tool still only inspects; it does **not** stop Docker,
  mask systemd, write SQLite or approve the release.

The current LXC110 contains actual root-owned executable
`/usr/bin/docker` and `/usr/bin/systemctl` (0755).
The isolated tests use fake binaries only within
root-owned private scratch directories. Their results
must never be confused with verified real owner state.

## Actual QA results

**12 new strict tests PASS**, including a missing executable,
a PATH-selected world-writable parent, unsafe/non-executable
binary, symlink to Docker, symlinked parent, hardlinked
inode, nonroot-owned binary, root-controlled exact fake
command and failure on unlisted commands.

**387/387 cumulative strict QA tests PASS**, as root
(1 expected root/nonroot-specific skip) and UID65534
(7 expected root-only skips), Ruff format/E/F/I/UP/SIM,
new Python functions <=40 lines and width <=79.
The actual fake-Docker/fake-systemd rollback shell suite
also passed after the path validation change in LXC114.
No production source or service was changed.

## Important remaining gaps

This is **not** a cryptographic authentication of Docker,
the systemd binary, the running OCI, or any claimed
owner freeze. Root can still replace root-controlled
test binaries, and a command may be changed between
the stat and execution by another root process.
Production must use trusted fixed-root deployment paths
and an explicit signed rollout. Bash itself currently
invokes `python3` through its inherited PATH and may
inherit `PYTHONPATH`, `LD_PRELOAD`, `BASH_ENV`,
or other environment variables. These still need
separate sanitization and proof before any production
maintenance activation.

More importantly, all legacy manual/update/recover/
installer/watchdog/emergency/Docker-autorestart paths
need a coherent lifetime-held G/B/W protocol.
The user chose one physical NVMe: local A/B backups
are for software rollback, not hardware disaster
recovery. The final CURRENT signed SQLite+WAL cutover,
real Telegram longpoll closure, physical power-loss
acceptance, independent review and **explicit Owner
Approval including single-NVMe loss risk** are pending.
Public pinned PHOTO remains NO_GO.

### Offline QA commands

```bash
python3 scripts/legacy-rollback-frozen-owner-preflight.py -h
python3 scripts/legacy-rollback-frozen-owner-preflight.py
python3 -W error::ResourceWarning -m unittest discover \
  -s tests -p test_legacy_owner_exec_path_qa.py -v
```

**Do not merge or deploy based only on QA/CI.** Current
production is still old signed v0.5.21-hw,
Docker restart=unless-stopped, Compose enabled,
maintenance G absent; none of these checks pass the
complete release gate in that environment.
