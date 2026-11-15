# Issue #340: Docker auto-restart bypasses independent B advisory lock

**QA-ONLY, REVIEW-ONLY, PRODUCTION NO-GO.** This test never edits
the production application, Docker restart policy or host services.

## Independently observed defect

The installed production `shelly-voltage-bot` Docker container
has `restart=unless-stopped`. The systemd
`shelly-voltage-compose.service` independently runs
`apply-update.sh --recover` in `ExecStartPre`, and
`start-compose.sh` in both `ExecStart` and `ExecReload`.
`install.sh`, `rollback.sh` and an optional emergency restore
path can also launch Docker. Existing updater mutex A does not
globally fence Docker autonomous restarts.

On **networkless LXC114** (Debian 13, Docker 26.1.5), a private
root-owned maintenance B inode was locked **EXCLUSIVE** by the
host QA process. A separate disposable Docker container was
started with actual `--restart unless-stopped`, using an
exact cached local OCI image ID and an inert Python counter.
The worker recorded one attempt and exited nonzero twice,
causing Docker to restart it automatically twice **while B EX
remained held**. It recorded **three counter-only starts**, then
remained running until QA cleanup.

| QA observation | Result |
| --- | --- |
| B maintenance lock | Held exclusive the entire test |
| Docker restart policy | Actual unless-stopped |
| RestartCount | 2 |
| Attempt counter under B EX | 3 |
| OCI code | Inert Python only, no app.Storage or SQLite |
| Network | none |
| Production Docker/SQLite/Telegram | Not accessed |
| Cleanup | Restart disabled, labelled container removed |

**Result: BYPASS_CONFIRMED.** A B lock protects only cooperating
code; it cannot fence Docker's built-in restart manager.

This does **not** mean the live bot has already restarted during
maintenance. Production was only inspected read-only; its actual
RestartCount was zero. The evidence instead proves a *potential*
release/restore race if the same policy is retained.

## Reproduce on the exact isolated guest

The CLI is inert by default and supports `-h`/`--help`:

```bash
python3 scripts/disposable-docker-restart-owner-qa.py
python3 scripts/disposable-docker-restart-owner-qa.py --help

python3 -W error::ResourceWarning -m unittest discover \
  -s tests -p test_disposable_docker_restart_owner_qa.py -v

# ONLY inside verified isolated LXC114, with explicit QA consent:
python3 scripts/disposable-docker-restart-owner-qa.py \
  --run-disposable \
  --confirm OFFLINE_DOCKER_RESTART_OWNER_BYPASS_QA_ONLY
```

Real execution requires root, exact hostname
`shelly-docker-reboot-qa`, network interfaces **only `lo`**,
the original QA writer **stopped with restart=no**, both QA
systemd owners disabled, and the fixed locally cached image ID.
No image pull, credentials, GitHub, Shelly connection or Telegram
action occurs.

The disposable container uses `--network none --read-only`,
non-root user 10001, cap_drop ALL, no-new-privileges, CPU, memory
and PID limits, and **only one synthetic counter directory
bind-mount**. The parent holds B EX for the entire observation.
Finally it checks the disposable ownership label, disables
restart policy and removes only its own container. The private
scratch directory is deleted. Initial and repeat real QA runs
both passed with two automatic Docker restarts.

**13/13 independent unit tests PASS**, strict ResourceWarning,
Ruff 0.14.2 quality PASS, PEP8, functions <=40 lines and
source width <=79 columns PASS.

## Production owner graph: independent review required

| Writer starting actor | Required future owner-safety contract |
| --- | --- |
| Docker daemon `unless-stopped` | Remove independent restart capability under approved maintenance; test actual container/daemon/host reboot fencing |
| Compose systemd ExecStartPre | Separate bounded recovery phase acquiring A; no B EX inside parent-held B SH deadlock |
| Compose ExecStart | Guard B SH and ensure pending/journal verified BEFORE any start |
| Compose ExecReload | Same B SH gate, fail closed on concurrent B EX |
| Manual updater apply | B EX -> A EX, full data-safe quiescence and durable journal |
| `install.sh` / `start-compose.sh --build` | Never bypass owner gate; no direct start under B EX |
| `rollback.sh` | B EX -> A EX before any DB/source changes; preserve whole CURRENT history |
| Emergency restore path | Explicitly account for re-enabling restart policies and start/stop |
| Timers and watchdogs | Verify they cannot launch a writer while maintenance or pending state exists |

Advisory flock alone is insufficient. A safe reviewed rollout
needs **one real authority over starts**, a controlled handoff
from maintenance B EX to guarded B SH start with no independent
Docker auto-restart in the gap, and no lock-order deadlock.
The present LXC114 test proves a bypass, **not a complete fix**.

## Release blockers

Current live source remains 0.5.15-hw vs signed immutable runtime
0.5.21-hw. Existing candidate PRs #336, #358 and #359 are not
approved for deployment. Exact signed source + full CURRENT
SQLite history A/B with independent restore, actual boot recovery
and whole-owner fencing, real signature trust, physical host power
loss and explicit Owner Approval are required independently.
Pinned public Telegram TEXT-to-PHOTO is another separate NO-GO.

**Do not change live Docker `restart`, Compose, or application
metadata on the basis of this QA proof alone.**
