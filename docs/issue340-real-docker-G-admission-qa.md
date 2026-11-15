# Issue #340 — actual offline Docker admission G/B/W and maintenance B/A

**QA-only, stacked onto Draft PR #366. No production integration or
release authorization.**

This stage moves the **synthetic Unix process** gate-order experiment
from #366 into **real disposable Docker containers** on the exact
networkless LXC114 guest. It tests cross-host/container flock behavior
using nonroot Python workers, but it **does not** run real production
Shelly, a signed release, Telegram or the actual updater.

## Real process sequence

All G, B, W and A lock files are root-owned, stable, mode0640,
group10001 in an invented private scratch directory. They are mounted
read-only into nonroot UID10001:10001 Docker, with `--network none`,
`--restart no`, `--read-only`, `--cap-drop ALL`,
`no-new-privileges`, fixed cached local test image and
exact owner labels. Events and SQLite are private disposable mounts.

1. First Docker container acquires **G SH → B SH → W EX**,
   releases G SH after holding B/W, creates one invented SQLite
   row, and remains alive with B/W held.
2. The LXC114 host acquires **G EX while the existing container
   still holds B SH and W EX**. A second Docker container tries
   G SH and exits with `DENIED_G`, without touching SQLite.
3. Under the held G EX, host sends SIGTERM to the first
   owner-verified QA container. An explicit SIGTERM handler is
   needed for synthetic Python running as container PID1;
   without it, the first experimental run correctly FAILED
   its bounded stop check. The handler was added and replayed.
4. Once first container stops, host acquires **B EX then A EX**;
   invented SQLite still has exactly one row.
5. Host releases A EX, B EX and G EX. A third disposable
   Docker writer is admitted, acquires B SH/W EX and inserts
   exactly one additional row. Final synthetic SQLite count is 2.
6. QA disables restart and removes **only** three exact
   owner-labelled containers and all scratch.

**Actual independent Docker acceptance PASS; repeated.**
Zero disposable containers or scratch directories remain.
The original QA signed writer is stopped with restart=no;
original QA systemd units disabled; LXC114 has only loopback.
No production device, app, data, network or Telegram was used.

## Tests and commands

- **14/14** new default-inert unit tests PASS. Exact CLI consent,
  cached image/nonroot/offline flags, G-before-B-before-W ordering,
  explicit PID1 SIGTERM, rejection before SQLite, fake Docker start
  failure, unowned resource refusal and cleanup are covered.
- **105/105** combined stacked QA tests PASS under both root
  and UID65534. Ruff E/F/I/UP/SIM and format PASS, all new
  functions <=40 lines and source width <=79 characters PASS.
- A regression in the test's mocked monotonic clock initially
  caused an unbounded test wait; the exact QA job was cancelled,
  monotonic was corrected to advance, and the suite was rerun
  successfully.

Default inert:

```bash
python3 scripts/disposable-docker-gate-handoff-qa.py --help
python3 scripts/disposable-docker-gate-handoff-qa.py
python3 -W error::ResourceWarning -m unittest discover \
  -s tests -p test_disposable_docker_gate_handoff_qa.py -v
```

Explicit isolated LXC114 only:

```bash
python3 scripts/disposable-docker-gate-handoff-qa.py \
  --run-disposable \
  --confirm OFFLINE_REAL_DOCKER_G_B_W_A_QA_ONLY
```

## Critical production NO-GO

This proves that a **cooperating in-container process** can enforce
short G admission and hold B/W until shutdown against a host
maintenance G/B/A procedure. The actual signed production Shelly
image v0.5.21-hw is **not** running this G protocol and the candidate
`writer_fence.py` in #363/#364 still has no G; this test runs
synthetic Python, not patched real app. Do not conflate a test
container with real production writer ownership.

The following remain mandatory: independently reviewed
full owner state machine for Docker daemon automatic restarts,
systemd prestart/start/reload, manual Compose, updater/recover,
installer, rollback and emergency restore; nonreplaceable
root-owned lock inodes; deadlock-free existing A serialization;
whole CURRENT signed source plus WAL-complete SQLite A/B on
independent physical failure domains; genuine Telegram long-poll
teardown; host power-loss/reboot; and **explicit Owner Approval**.
Production SQLite/containers/service/lock policy were not modified.
Public Telegram pinned PHOTO remains separately NO-GO.

Related: Issue #340, drafts #363–#366.
