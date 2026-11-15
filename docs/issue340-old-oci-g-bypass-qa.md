# Issue #340 — real legacy OCI bypass under maintenance G EX

**NEGATIVE safety acceptance on isolated QA. Not a release authorization.**

This stage is stacked on draft PR #373 (Pro3EM readiness QA)
and earlier issue #340 drafts. It demonstrates why the
**old unfenced production-generation image** must be stopped
and its autonomous restart suppressed **before** maintenance
can rely on new G/B/W/A admission. Neither the old currently
running bot nor its Docker policy is changed by this test.

## Actual isolated LXC114 negative result

The fixed-host guest guard requires exact
`shelly-docker-reboot-qa`, root, only loopback NIC,
original signed QA writer STOPPED with restart=no,
two original QA systemd units disabled, and exact
already cached local image ID
`sha256:59d8b7c6d7c672958893392dcf09c40da96d664e1b26d6364551e69bcf3a883b`.

The disposable one-time script creates only an invented
root-owned `G` lock and separate SQLite directory. It
acquires **host G EX** before starting a nonroot UID10001
Docker instance from that cached, earlier 0.5.21-hw
application image in its previous **unguarded** mode:

- `--network none`, `--restart no`, `--read-only`,
  no ambient Linux capabilities or new privileges
- Shelly host points to **127.0.0.1**; Telegram token
  and chat ID are blank
- SQLite points exclusively to
  `/synthetic/legacy.sqlite3` under a disposable mount
- All arguments are fixed by the script; no external
  image pulls, network or live database mounts

**Observed in a real Docker run:** while the host
continued holding **G EX**, the cached legacy
`/app/app.py run` created an invented SQLite database,
completed **six schema tables**, and passed
`PRAGMA quick_check=ok`. The old code was not
participating in G admission. In other words, a
new G EX lock does **not** stop an older, uncooperative
instance of this application generation from opening
SQLite.

This test is a *negative proof of a deployment hazard*,
not a desirable condition and not a declaration that
production has corrupted any data.

The first reusable script revision checked SQLite as
soon as its filename appeared and correctly failed
because the schema transaction was not yet committed.
The script now waits for a **complete, valid schema**
under a bounded deadline. Actual replay PASS,
with exact QA container and scratch cleaned.

## Regression and commands

- **13/13** new default-inert unit safety tests PASS.
  They check exact offline consent, guard call order,
  cached-only image, disabled Telegram/network,
  non-G legacy config, kernel G locking, SQLite
  schema completeness, bounded failure and label-gated
  container cleanup.
- Across the stacked isolated workspace,
  **192/192** strict unit tests PASS as root and
  nonroot UID65534. Ruff format E/F/I/UP/SIM,
  all new functions <=40 lines and width<=79 PASS.
- No leftover QA Docker containers or scratch directories.
- Actual Docker bypass proof requires explicit
  isolated LXC114 consent; GitHub CI runs safe unit tests.

```bash
# Default inert; no external activity:
python3 scripts/disposable-legacy-oci-g-bypass-qa.py --help
python3 scripts/disposable-legacy-oci-g-bypass-qa.py

# Safe default-inert unit tests:
python3 -W error::ResourceWarning -m unittest discover \
  -s tests -p test_disposable_legacy_oci_g_bypass_qa.py -v

# Exact networkless LXC114 only:
python3 scripts/disposable-legacy-oci-g-bypass-qa.py \
  --run-disposable \
  --confirm OFFLINE_LEGACY_OCI_G_BYPASS_QA_ONLY
```

## Explicit release blocker

Before enabling `required-g` in a new signed image,
a release owner must demonstrate that no old image
can be started or restarted by Docker daemon,
the existing systemd unit, manual Compose, the updater,
`--recover`, installer, rollback, watchdog or emergency
recovery. A new in-container G gate **cannot protect**
against older running processes that ignore it.

An approved maintenance migration must first install
and test **durable external owner controls**, freeze
all old admission paths, remove Docker automatic
restart for old instances, stop/join all actual writer
processes and verify no live SQLite transactions;
then acquire B EX -> updater A EX under G EX,
prepare independently restorable whole CURRENT signed
source+SQLite including WAL on separate physical
media, and activate only the reviewed signed G-aware
image and owner controls. Incorrect control flow must
remain fail-closed and reversible with verified backups.
A fallback to an older unguarded image must not
silently restart SQLite writers.

Actual production LXC110 still runs the unchanged
signed 0.5.21-hw OCI with `restart=unless-stopped`.
Current installed source differs from runtime and
production has no G lock. LXC110 and LXC114 share
one physical Proxmox host. Hardware-independent
power-loss recovery, real Telegram long-poll
shutdown, independent security review and explicit
Owner Approval are not accepted yet.

**PRODUCTION NO-GO.** The public pinned Telegram
TEXT-to-PHOTO transition is also independently NO-GO.
