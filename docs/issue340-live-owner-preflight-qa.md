# Issue #340 — live read-only legacy owner preflight

**Draft/QA-only. This scanner cannot issue production Owner Approval.**

Previous evidence: PR #374 confirmed that a cached old unfenced OCI
could create synthetic SQLite under maintenance G EX. PR #375
demonstrated the narrower positive result that **one disposable old
Docker owner** can have its `unless-stopped` policy changed to
`no` before stop, with a stable stopped state afterward.

This PR adds `scripts/legacy-owner-live-preflight.py`, a **default
inert** local script that checks actual current owner evidence
without altering anything. Only explicit `--audit` invokes read-only
commands (`docker inspect`, `systemctl cat`) and file `stat` /
source reads. It does not execute updater, Compose, restart, stop,
rollback, backup, SQLite write, or Telegram. It prints only fixed
risk codes; raw environment/secrets and unit content are never
echoed.

## Live LXC110 read-only audit — 2026-11-15

Executed by Gateway as `python3 - --audit --root
/opt/shelly-voltage`, streaming this exact QA script to stdin.
The script was **not installed** on production.

Exit **3** with **11** observed blocking findings:

| Area | Live finding |
| --- | --- |
| Docker | `OLD_WRITER_NOT_QUIESCENT` |
| Docker | `DOCKER_AUTORESTART_NOT_FROZEN` |
| systemd | `RECOVER_PRESTART_UNGATED` |
| systemd | `COMPOSE_START_UNGATED` |
| systemd | `COMPOSE_RELOAD_UNGATED` |
| source | `COMPOSE_AUTORESTART_UNGATED` |
| source | `UPDATER_RECOVERY_A_ONLY` |
| source | `DETACHED_START_HANDOFF_UNREVIEWED` |
| source | `ROLLBACK_OLD_SOURCE_UNREVIEWED` |
| admission | `PRODUCTION_G_INODE_MISSING` |
| backup | `BACKUP_SAME_FILESYSTEM_AS_CURRENT_SQLITE` |

These are **known pattern checks, not exhaustive positive acceptance**.
An installed script whose wording changed may escape a lexical match;
such absence does not prove it is guarded. Other startup actors,
in particular manual starts, installer, watchdog, emergency restore
and other timers, require independent owner inventory and acceptance.

No real Docker policy was changed and no service stopped. The
production signed container remained healthy with zero restarts.
G does not exist on the installed host. Current SQLite and archived
backups are on the same filesystem/physical NVMe; an alternative
filesystem alone would not prove physical independence.

## Interface and acceptance results

The script is inert without `--audit` and supports `-h/--help`.
Exit code **3** means known runtime owner blockers. Exit code **4**
means static/dynamic checks appeared clean but **independent manual
review and full acceptance are still required**. There is no GO
return from this audit. It cannot be used to authorize a deployment.

- **20 new unit safety tests PASS**. They check default-inert mode,
  Docker owner/restart policy, malformed command responses, systemd
  prestart/start/reload detection, missing script/symlink refusal,
  G inode ownership/mode, same-filesystem backup, no false physical
  independence when st_dev differs, and stable nonzero report codes.
- **231/231 cumulative QA tests PASS** on root and nonroot UID65534.
  A test fixture running as nonroot produces an untrusted-owner G inode,
  correctly rejected as `UNTRUSTED`; the unit assertion now accepts
  either `UNTRUSTED` or `WRITABLE` (both are fail-closed).
- Ruff format/E/F/I/UP/SIM, PEP8, new functions <=40 lines and
  source width <=79 characters PASS.

Default inert:

```bash
python3 scripts/legacy-owner-live-preflight.py -h
python3 scripts/legacy-owner-live-preflight.py
python3 -W error::ResourceWarning -m unittest discover \
  -s tests -p test_legacy_owner_live_preflight.py -v
```

Explicit read-only audit (produces nonzero NO_GO on current host):

```bash
python3 scripts/legacy-owner-live-preflight.py --audit \
  --root /opt/shelly-voltage
```

## Release decision

This script does **not** freeze any actor. Actual migration needs
physically independent, whole-CURRENT signed-source plus
WAL-complete SQLite A/B restored backups; reviewed external
freeze of **all** old start/restart paths, G EX, verified complete
old shutdown and quiescence, B EX then updater A EX without
circular systemd prestart ownership, trusted new signed gated
image, complete rollback/recovery proof, real Telegram long-poll
shutdown and physical power-loss acceptance. Explicit Owner
Approval is mandatory. Do not silently roll back to old
ungated OCI. Public pinned TEXT-to-PHOTO remains separately NO_GO.

**Do not merge/deploy based on green CI. No live source,
SQLite, Docker, restart policy or Telegram was modified.**
