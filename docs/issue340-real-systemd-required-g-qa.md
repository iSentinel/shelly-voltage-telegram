# Issue #340 — real transient systemd -> Docker -> app required-g QA

**QA-only, stacked Draft on #370. No live service modification.**

This stage proves one **cooperating** systemd startup path can
respect an external maintenance G EX gate, while the real
candidate Shelly `app.py` independently enforces in-container
G SH -> B SH -> W EX admission and lifetime B/W ownership.

It is not the production `shelly-voltage-compose.service`,
and does **not** modify that service, its installed scripts,
actual Docker restart policy, live SQLite or Telegram.

## Isolation and consent

The default-inert
`scripts/disposable-systemd-real-app-required-g-qa.py`
only runs with explicit
`--run-disposable --confirm
OFFLINE_SYSTEMD_REAL_APP_REQUIRED_G_QA_ONLY`.
Its imported sibling guards require exact
`shelly-docker-reboot-qa` LXC114 (root, **only loopback**),
original signed QA writer STOPPED/restart=no,
two original QA systemd owners DISABLED,
and a fixed existing cached local OCI image.

Every test uses a fresh 0700 QA root with synthetic
root-owned G/B/W files (group10001, mode0640),
private code containing the **real candidate app.py and
writer_fence.py**, invented SQLite, no Telegram credentials
and dummy `SHELLY_HOST=127.0.0.1`.

The transient units have unique names
`svt-qa-systemd-g-*`; no existing production or QA
unit is edited. Real systemd command applies:

- `Type=oneshot`, `--wait --pipe --collect`
- `PrivateNetwork=yes`, `ProtectSystem=strict`
  and `ReadWritePaths` scoped to QA scratch
- `/usr/bin/flock --shared --nonblock G`
  preceding `/usr/bin/docker run -d`
- Disposable owner-labelled Docker with
  `--network none`, `--restart no`, read-only root,
  UID10001, caps dropped, no new privileges and
  only synthetic code/lock/SQLite mounts.

## Actual LXC114 acceptance

**Case 1: maintenance G EX already held.**
The transient `systemd-run` fails before Docker
container creation. No SQLite file appears.

**Case 2: maintenance G EX released.**
Real transient systemd unit starts the real candidate
Shelly app in an offline Docker container. Application
acquires G SH -> B SH -> W EX and releases G SH after
admission. A separate host G EX probe succeeds while
host B EX and W EX probes are denied by the live app.
Real Docker SIGTERM exits **code 0**, all G/B/W locks
are released and synthetic SQLite `quick_check=ok`.

After repeated actual runs: **zero** transient units,
**zero** disposable containers and **zero** scratch
directories remain. Original QA writer remains stopped;
original QA units remain disabled.

### Unit tests and CI contract

- **16/16 new default-inert safety tests PASS**:
  exact user consent, fixed-host import, unit naming,
  `PrivateNetwork`/G `flock` order, denied G EX before
  Docker/SQLite, B/W lease verification, owner-label
  SIGTERM and clean exit, scratch SQLite integrity and
  cleanup ordering.
- Combined stacked QA workspace **148/148 tests PASS**
  under root and UID65534, Ruff 0.14.2 format plus
  E/F/I/UP/SIM, new functions <=40 lines and line
  width <=79 characters PASS.
- GitHub CI runs **only safe unit tests**. Actual
  Docker/systemd tests run on authorized LXC114 only.

### Reproduction

Default inert:

```bash
python3 scripts/disposable-systemd-real-app-required-g-qa.py -h
python3 scripts/disposable-systemd-real-app-required-g-qa.py
python3 -W error::ResourceWarning -m unittest discover \
  -s tests -p test_disposable_systemd_real_app_required_g_qa.py -v
```

Exact offline LXC114 only:

```bash
python3 scripts/disposable-systemd-real-app-required-g-qa.py \
  --run-disposable \
  --confirm OFFLINE_SYSTEMD_REAL_APP_REQUIRED_G_QA_ONLY
```

## Production NO-GO: what this does *not* establish

The test's transient `flock G SH` covers a cooperating
one-time startup invocation. A detached `docker run -d`
may return before its app acquires B/W; the **separate
in-container required-g** check is mandatory to
avoid the delayed-start admission gap. This test does
not establish that production Docker daemon restart,
systemd boot recovery, ExecReload, manual Compose,
updater, installer, rollback or emergency restore
all respect the same lock identities and state machine.

Read-only LXC110 audit shows production unit still
runs `apply-update.sh --recover` in ExecStartPre,
`start-compose.sh` in ExecStart/ExecReload, and
`docker compose stop` in ExecStop, with older updater
A-only locking. There is currently no
`/run/shelly-owner/G` on production. Enabling
`required-g` without staging trusted G/B/W/A
and making **every** owner cooperate would
fail closed and disrupt monitoring.

Whole **CURRENT signed source+WAL-complete SQLite
A/B** independently restored on separate physical
storage, real Telegram long-poll quiescence,
physical power-loss acceptance, independent
security review and explicit Owner Approval remain
mandatory. Production LXC110 and QA LXC114 share
one physical Proxmox host. Public pinned Telegram
PHOTO remains a separate NO-GO.

**Do not merge, deploy, restart production or change
Telegram status solely because these QA checks pass.**
