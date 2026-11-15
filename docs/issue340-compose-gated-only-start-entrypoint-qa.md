# Issue #340 — guarded-only Compose start path (isolated QA)

**Stacked Draft on PR #381. Source changes ONLY. Production NO_GO.**

Prior real LXC114 QA proved that the old cached 0.5.21-hw OCI
can initialize SQLite **while host maintenance G EX is held**.
The old production Docker daemon currently has
`restart=unless-stopped` and other independent start owners.
Consequently, adding a new G lock or a one-time `flock`
around a detached Docker CLI does not protect the old image.

This changes **one** new-code start entrypoint:
`scripts/start-compose.sh` now refuses `--build`,
requires an existing pinned OCI override, then executes
`scripts/legacy-compose-start-gate.py --check` **before
the first `docker compose` invocation**. The only permitted
Compose action after a successful precheck is
`config -q` followed by `up -d --no-build`.
The earlier unpinned/build fallback branches are removed
in this candidate source. `--help` remains harmless.

## Fail-closed local permit gate

The new default-inert Python gate requires explicit `--check`,
a root-owned 0700 admission directory, root-owned/one-link regular
G/B/W lock files at mode0640, and a private root-owned
`compose-permit.json` at mode0600. It verifies an exact pinned
GHCR image ref and a pinned YAML override referencing that
same digest, rejects symlinks/invalid permissions/multiple
images, requires a `gated-only` permit with
`writer_protocol=G_SH_B_SH_W_EX` and expiry **within
five minutes**, and obtains a nonblocking kernel G SH
during admission. Maintenance G EX, missing/expired permit,
or incomplete metadata produces a nonzero denial.

**IMPORTANT limitations:** This is a root-controlled local
admission *claim*, **NOT cryptographic `cosign verify`**
of the image, not actual verification that the selected OCI
contains new writer code, and not Owner Approval. Its G SH
lease ends when the precheck returns; detached Compose runs
afterwards. Only a separately reviewed, signed **G-aware**
app may acquire G/B/W **before SQLite** and hold B/W through
shutdown. The old uncooperative OCI ignores these locks.

This patch does **not** guard `rollback.sh` which
sources `start-compose.sh` and may invoke `compose_cmd`
directly, nor `apply-update.sh`, `--recover`,
installer, manual `docker compose`, Docker autonomous
restart, watchdog, or emergency recovery. The shell
`SVT_ROOT` and `SVT_OWNER_GATE_DIR` overrides exist
for controlled offline testing, but must be pinned or
validated when the global signed deployment is integrated.
All-owner coverage and signed-source provenance are still
release blockers. No release GO claim can be made.

## Actual isolated evidence

In loopback-only LXC114 with a private root-owned
disposable fixture and a fake `docker` binary prepended
to PATH (no real Docker call, no Shelly/Telegram/network):

- Missing permit -> exit3, **zero** Docker stub calls.
- `--build` -> exit3, **zero** Docker calls.
- Host maintenance G EX -> exit3, **zero** Docker calls.
- Exact non-expired gated-only permit plus pinned image
  -> only fake `config -q` and `up -d --no-build`.
- All scratch cleaned and original QA Docker remains stopped.

**17/17 new guard unit tests PASS** for default inert,
invalid/missing/multiple image, stale/overlong permit,
wrong writer protocol, root inode permission/link/parent
rules, actual G kernel lock conflict and CLI fail-closed
statuses. **331/331 cumulative strict QA tests PASS**
both root and UID65534, Ruff E/F/I/UP/SIM/format,
functions<=40/source width<=79. The fake Docker
positive test does *not* prove a signed OCI deployment.

### Commands

```bash
python3 scripts/legacy-compose-start-gate.py --help
python3 scripts/legacy-compose-start-gate.py
python3 -W error::ResourceWarning -m unittest discover \
  -s tests -p test_legacy_compose_start_gate.py -v
bash -n scripts/start-compose.sh
```

The changed `start-compose.sh` intentionally fails when
the production admission directory/permit is not installed.
**Do not install or enable it by itself**: the existing
production unit, recovery/start/reload, installer and
rollback must be migrated together under a separately
accepted plan, or automatic recovery could be blocked.

## Overall release decision

The user chose one NVMe without an independent physical
backup medium. Two actual local *preparation* copies,
the immutable verifier and the new default-inert builder
have passed tests, but the final stopped-writer CURRENT
cutover is not yet accepted on production. All existing
old-start owners must first be externally frozen and
fully quiesced before reliable G EX→B EX→A EX maintenance.

Production signed OCI, Docker restart policy, systemd,
SQLite, backup history and Telegram remain unchanged.
Real Telegram longpoll teardown, controlled rollback,
power-loss acceptance, independent review and **explicit
Owner Approval including whole-NVMe-loss risk** remain.
Public pinned PHOTO status is a separate NO_GO.

**DO NOT MERGE OR DEPLOY BASED ON QA ALONE.**
