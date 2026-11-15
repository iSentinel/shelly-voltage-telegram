# Issue #340 — repeatable real Shelly `required-g` Docker acceptance

**QA-only stacked Draft on #369. Production release NO-GO.**

The previous required-g acceptance used a one-time direct
LXC114 Docker experiment. This stage installs a **default-inert**
reproducible standalone QA script and 12 unit tests so reviewers
can rerun the real app lifecycle without touching production.

## Preconditions and hard isolation

The script imports only its adjacent pre-existing
`disposable-real-app-sigterm-qa.py` and
`disposable-writer-fence-container-qa.py` guards.
Execution is refused unless the exact
`--run-disposable --confirm` token is supplied.
The guard verifies:

- Root in exact LXC114 `shelly-docker-reboot-qa`
  with **only loopback**. No real Shelly/Telegram network.
- Original signed QA Docker writer STOPPED, restart=no.
- Original QA Compose and watchdog units DISABLED.
- Only exact immutable cached QA Docker image ID.
- Private synthetic B/W/G mode0640 root UID0,
  group10001 in a fresh 0700 scratch tree; no live SQLite.
- QA containers have exact checked owner labels,
  nonroot UID10001, no network, no privileges,
  restart=no and read-only root filesystem.

It uses the **actual candidate `app.py` and
`writer_fence.py`**, copied into a private read-only
code mount, with VERSION from the locally verified
cached image. The app uses a synthetic SQLite
`/synthetic/qa.sqlite3` and dummy loopback Shelly.
Telegram token and chat ID are both empty.

## Test procedure

1. Hold maintenance **G EX on the host** and run
   the actual candidate `app.py run` in a disposable
   **foreground `docker run --rm`** process.
   Require nonzero exit with exact
   `owner_fence_already_held` reason and
   **no SQLite file creation**.
2. Release host G EX; run the same candidate real app
   in an owner-labelled disposable detached Docker
   container with `SHELLY_WRITER_FENCE=required-g`.
3. Verify app initialized only invented SQLite.
   Independent host G EX succeeds, but host
   B EX and W EX probes fail for the active writer.
4. Send real Docker SIGTERM and require exit code 0.
   Verify B/W and G are freely acquired after app exit,
   SQLite `quick_check=ok` and all temporary
   containers and directories deleted.

**Actual LXC114 result: PASS**, including synthetic SQLite
six tables and zero leftovers. There is no Telegram
long-poll or real Shelly network in this experiment.

### Important QA instrumentation finding

This QA Docker host's logging driver does not support
reading `docker logs`. The first reusable test tried
to inspect logs after a detached denial and correctly
FAILED its proof requirements. This was not a writer
defect. The safe regression uses a **foreground**
`docker run --rm` and directly captures actual
`app.py` stdout/stderr and exit code; it no longer
depends on Docker's log driver.

## Validation

- **12/12 new strict, default-inert QA unit tests PASS**.
  Unit cases check fixed host guard, exact explicit
  consent, required-g env, foreground failure,
  exact denial reason, fail-closed synthetic DB creation,
  bounded waiting and owner-labelled cleanup.
- **132/132 cumulative QA tests PASS** under root
  and unprivileged UID65534; Ruff 0.14.2
  format + E/F/I/UP/SIM PASS, all new functions
  <=40 lines, source lines <=79 columns.
- The real Docker scenario runs only after
  **explicit QA consent** on LXC114; GitHub Actions
  runs the safe unit tests, not the real integration.

### Reproduce (default inert)

```bash
python3 scripts/disposable-real-app-required-g-qa.py -h
python3 scripts/disposable-real-app-required-g-qa.py
python3 -W error::ResourceWarning -m unittest discover \
  -s tests -p test_disposable_real_app_required_g_qa.py -v
```

Actual isolated LXC114 only, after verifying original
QA writer stopped and no network interfaces except lo:

```bash
python3 scripts/disposable-real-app-required-g-qa.py \
  --run-disposable \
  --confirm OFFLINE_REAL_APP_REQUIRED_G_ADMISSION_QA_ONLY
```

## Unresolved release gates — NO-GO

Even though this QA script runs the **real app source**,
it is still an isolated source candidate, not the signed
production runtime or any real Telegram network.
It cannot demonstrate control over actual uncooperative
Docker/systemd/Compose/update/install/rollback/emergency
actors; existing production is not running a G-gated
writer and retains `restart=unless-stopped`.

All production owner paths need reviewed global
G EX -> stop/join writers -> B EX -> A EX
semantics, stable root-owned inode identity and
crash/recovery acceptance. Whole **CURRENT** signed
source+SQLite+WAL A/B must be independently
restorable across physically separate failure domains.
Real Telegram long-poll shutdown, physical power-loss
and explicit Owner Approval are still pending. The
public pinned Telegram TEXT-to-PHOTO change is
separate and also NO-GO.

**Do not merge or deploy solely on green GitHub CI.**
