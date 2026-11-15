# Issue #340 — isolated old Docker owner freeze before stop

**QA-only single-owner acceptance. Release decision remains NO_GO.**

This stage extends the existing negative legacy OCI proof in
Draft PR #374. A legacy, uncooperative image can initialize
SQLite while host maintenance holds G EX. Stopping the image
without disabling autonomous Docker restart can introduce
a new writer after the apparent stop. A G lock does not
protect against the old image.

## Actual isolated LXC114 proof

The default-inert QA script uses the pre-existing exact
fixed-host guard for `shelly-docker-reboot-qa`, root with
only loopback, stopped original signed QA writer
(restart=no), both original QA systemd units disabled
and exact previously cached local old application image.
No network, Telegram credentials, live SQLite or source
replacement is used.

The disposable old OCI is started as nonroot UID10001
with read-only root filesystem, no capabilities, no
privilege escalation, and only invented SQLite+lock
mounts. It waits for a committed synthetic SQLite
schema, not merely an empty database filename.

Actual Docker sequence on this one **QA-labelled** object:

1. Start the old unguarded application, verify
   committed invented SQLite.
2. Set Docker restart policy to `unless-stopped`
   using `docker update`, then independently read
   `.HostConfig.RestartPolicy.Name` to confirm it.
3. Acquire host **G EX**. The old writer still
   runs and an independent B EX probe succeeds:
   the legacy OCI ignores both G and B. This is
   evidence of the dangerous old-owner gap.
4. Set Docker restart policy to **`no` and
   verify it independently**, *before* signalling
   the existing old writer to stop.
5. Require the process to exit, then re-read
   container state, restart policy and restart
   counter repeatedly over a bounded observation
   window. All stay stopped/no/unchanged.
6. Require that host B EX/W EX can be acquired.
   Remove only exact owner-labelled disposable
   Docker and synthetic scratch.

**Real Docker PASS, repeated**. The original signed
QA writer remains STOPPED restart=no; zero test
containers or temporary directories remain.

## Unit and quality tests

- **19/19 new safety unit tests PASS**, including
  default inert/explicit consent, exact QA guard,
  real kernel G contention, owner-label refusal,
  malformed Docker state refusal, policy
  verification and mismatch failures,
  **stop-with-restart-enabled rejection**,
  unexpected restart count/running-state
  rejection, bounded observation, cleanup and
  explicit `release_decision=NO_GO`.
- Combined cumulative stacked QA:
  **211/211 strict tests PASS** under root
  and UID65534. Ruff format/E/F/I/UP/SIM,
  all new functions <=40 lines and source
  width <=79 characters PASS.
- Actual Docker integration runs only on LXC114
  with an exact offline confirmation token;
  GitHub CI runs the unit suite, not real Docker.

Default inert:

```bash
python3 scripts/disposable-legacy-owner-freeze-qa.py --help
python3 scripts/disposable-legacy-owner-freeze-qa.py
python3 -W error::ResourceWarning -m unittest discover \
  -s tests -p test_disposable_legacy_owner_freeze_qa.py -v
```

Exact offline LXC114 only:

```bash
python3 scripts/disposable-legacy-owner-freeze-qa.py \
  --run-disposable \
  --confirm OFFLINE_LEGACY_DOCKER_FREEZE_BEFORE_STOP_QA_ONLY
```

## This is not an all-owner freeze

The experiment covers only **one disposable Docker
container that the test itself owns**. It does not
disable any real production Docker auto-restart, timer,
Compose systemd unit, manual shell, updater,
`--recover`, installer, rollback, watchdog or
emergency actor. On production, some of those are
still able to start the old ungated image.

In particular, B EX being freely acquirable while
the old writer is running demonstrates why obtaining
B EX must **never** by itself count as proof of
quiescence of a legacy image. New admission locks
must be preceded by a separate verified complete
freeze of all old start paths and shutdown of
all legacy writer PIDs.

The P0–P8 migration requirements remain in
`docs/issue340-prod-migration-g-aware-owner-plan.md`.
Release also requires whole CURRENT signed source
plus WAL-complete SQLite independently restored
A/B on separate physical storage, review of all
owners and deadlock-free G EX→stop→B EX→A EX,
real Telegram long-poll shutdown, physical
power-loss acceptance and **explicit Owner Approval**.
Public pinned PHOTO remains a separate NO_GO.

**This PR does not authorize deployment or modify
production.** Live signed OCI, app code, Docker policy,
SQLite, Telegram and actual Shelly hardware remain
unchanged.
