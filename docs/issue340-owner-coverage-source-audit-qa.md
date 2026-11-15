# Issue #340 — fail-closed owner source coverage audit

**QA-only static safety gate. It can never authorize production.**

This stage is stacked on draft #371 and earlier drafts. The
`scripts/owner-coverage-source-audit.py` CLI is strictly read-only.
It inspects local source files but performs **no execution of shell,
Docker, systemd, SQLite, Telegram, SSH or external network**.

## Purpose

Real isolated QA has already demonstrated that a *cooperating*
in-container `required-g` writer can be admitted behind G SH
and hold B SH/W EX until shutdown, and that transient systemd
can refuse a Docker start under maintenance G EX.

That does NOT demonstrate that the **actual** deployed owners
all cooperate. One unguarded updater, installer, rollback,
emergency restore, manual Docker start or daemon auto-restart
can invalidate a partial single-writer proof.

The owner auditor therefore reports known unreviewed source
patterns as `NO_GO`. It is deliberately **not** an acceptance
oracle: an otherwise clean lexical scan returns
`REVIEW_REQUIRED`, never `GO`.

## Known owner sources and findings

Required local files: `compose.yaml`,
`scripts/apply-update.sh`, `scripts/start-compose.sh`,
`scripts/install.sh`, `scripts/rollback.sh`.
Missing files, symlinks and nonregular files are reported
`MISSING_OR_UNSAFE_SOURCE`; source is never executed.

On the **exact files fetched from GitHub** branch
`qa/issue340-real-systemd-required-g-20261115`
(the parent PR's revision), the command reported
**9 unresolved findings** and exit code **3**:

| Source | Unverified owner behavior |
| --- | --- |
| `compose.yaml` | Autonomous `restart: unless-stopped` |
| `apply-update.sh` | Only legacy updater A lock |
| `apply-update.sh` | `--recover` A-only boot recovery |
| `apply-update.sh` | Docker stop/handoff not globally fenced |
| `apply-update.sh` | SQLite WAL restore ownership unverified |
| `start-compose.sh` | Detached `compose up -d` admission handoff |
| `install.sh` | Re-enables Docker autonomous restart |
| `rollback.sh` | Replaces SQLite WAL/SHM |
| `rollback.sh` | Replaces mutable installed source files |

These are **review findings**, not evidence that these existing
paths have already corrupted data. Even a source file mentioning
G/B/W/A in a comment does not bypass the risk check.

### Exit statuses

- **3** — `NO_GO` with one or more known findings, including
  required source missing or unsafe.
- **4** — `REVIEW_REQUIRED` when known text patterns are
  absent; still not authorization to deploy.
- **0** — argparse `-h/--help` only, not the audit result.

The CLI prints only file paths and stable risk codes, without
printing secrets, source contents or environment variables.
It has no output file and no write operations.

## QA and reproduction

- **13/13 new unit tests PASS**, using invented checkout
  fixtures: missing/unsafe sources, symlink, no writes,
  actual regex coverage, stable nonzero exits, and
  clean-looking scripts never returning GO.
- **161/161 combined QA tests PASS**, root and nonroot
  UID65534. Ruff format E/F/I/UP/SIM PASS, new functions
  <=40 lines and width <=79.
- Actual GitHub source scan result: **9 findings, exit 3**.
  Each fetched source had its GitHub blob SHA recorded in
  the private QA inspection; the test files are in a
  scratch-only LXC114 fixture, not production.

```bash
python3 scripts/owner-coverage-source-audit.py --help
python3 scripts/owner-coverage-source-audit.py \
  --repo-root /path/to/checked-out-repo
python3 -W error::ResourceWarning -m unittest discover \
  -s tests -p test_owner_coverage_source_audit.py -v
```

**Important:** a nonzero audit exit is intentional.
Release tooling should **not** map either 3 or 4 to success.

## Remaining release blockers

A source-only scan is an incomplete inventory; it does not
establish correctness of actual systemd units, Docker daemon
restarts, emergency restore, network services or OS power loss.
A source change that removes a matched pattern can reduce
the observed findings but does not demonstrate full owner
integration or justify an automatic `GO`.

Production LXC110 currently runs a pinned signed v0.5.21-hw
image with Docker restart `unless-stopped` and no G lock file.
It has an installed host source mismatch. Do not enable
`required-g` or change image/Compose/restart policy without
an explicit approved migration and completed restore tests.

Release remains **NO-GO** until every start/stop/update/
rollback/recover/installer/manual/emergency path implements a
reviewed global noncircular admission protocol, lock inodes
remain immutable, whole **CURRENT signed source + SQLite
including WAL/SHM** A/B is independently restorable across
separate physical failure domains, genuine Telegram
long-poll/SQLite shutdown, physical power-loss testing,
independent review and **explicit Owner Approval**.
The public pinned Telegram PHOTO change independently
remains NO-GO.

This PR does **not** modify or restart production, and
does not alter any current signed OCI image, production
SQLite database or Telegram status.
