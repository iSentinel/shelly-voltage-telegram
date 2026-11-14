# Disposable systemd boot decision replay

**QA-only, stacked on PR #341, tracking Issue #340.**
This does not authorize a real reboot, source reapply, Docker start,
Telegram status change, or source/SQLite restoration.

## Purpose

The installed production systemd Compose service invokes
apply-update.sh --recover in ExecStartPre. A failed recovery may
correctly refuse stale history while also preventing service startup.
An automatic signed-image-safe continuation policy has not been
approved.

PR #341 provides a read-only boot decision audit that never starts
any writer. This follow-up tests that audit under the real transient
systemd ExecStartPre and ExecStart ordering, using only synthetic
private paths and invented immutable image metadata.

## Safety and commands

No operations by default:

    python3 scripts/disposable-systemd-boot-replay-qa.py --dry-run

Run only on a host with systemd and explicit disposable consent:

    python3 scripts/disposable-systemd-boot-replay-qa.py \
        --run-disposable \
        --confirm DISPOSABLE_BOOT_CONTINUITY_SYSTEMD_REPLAY_ONLY

Use -h or --help for usage. No caller-supplied container, service,
Docker image, database, source directory, or arbitrary command is
accepted. The harness never invokes Docker and never reads the real
Shelly SQLite. A random svt-qa-boot-replay unit is generated for
every test. The Python read-only audit reads only its private
synthetic state and fake runtime JSON.

The only ExecStart command is touch of a marker in the temporary
QA directory. It does not start any actual writer, network service
or Telegram operation. It checks both the systemd result and its
own journal reason code, so an unrelated systemd failure cannot be
mistaken for a passed negative test.

## Scenarios

| Case | Expected synthetic main ExecStart |
| --- | --- |
| clean | Executed |
| clean-again | Executed on second independent boot |
| pending | Not executed; pending marker retained |
| pending-wal | Not executed; new committed sample retained |
| source-mismatch | Not executed |
| missing-pin | Not executed; no local build fallback |
| corrupt-db | Not executed; no historical replacement |
| stopped | Not executed; boot start not authorized |

The test compares the full logical SQLite dump, including committed
WAL records and user_version/application_id. It separately hashes
synthetic source, receipt, runtime and pending state. It intentionally
does not require SQLite WAL/SHM sidecar files to have identical bytes;
read-only connections may update the sidecar representation without
changing any committed history.

All generated units are stopped/reset and scratch files removed.
There are no production paths in systemd ExecStartPre. If a test fails,
the harness returns a nonzero status; no PR CI success can count as
an automatic boot authorization.

## LXC110 acceptance on November 14, 2026

- All 8/8 real transient systemd startup decisions PASS.
- 22/22 isolated mocked unit tests PASS.
- Full project Ruff quality and functions <=40 lines PASS.
- Clean fixture can reach harmless test main ExecStart repeatedly.
- Valid pending marker, extra committed WAL row, source/image drift,
  no pinned override, corrupt SQLite or stopped writer prevent the
  test main ExecStart, preserving synthetic history and journals.
- Scratch fixtures and transient systemd units removed.
- Real Shelly Docker remains Healthy with zero restarts; production
  Compose, updater timers, SQLite, images and Telegram untouched.

## Limitations

These tests are a disposable systemd prestart **decision** replay.
They do not simulate a physical host reboot or electrical failure
with all real restart owners, prove WAL power-loss durability, verify
Cosign signatures, or exercise any approved automatic continuation.

A separate read-only Cosign v3.0.6 check verified the current pinned
release v0.5.21-hw image, but host sources remain v0.5.15-hw.
Actual production restart-owner audit remains BLOCKED.

Issue #340 still requires independently reviewed fail-closed
signed-image-safe continuation design, exact fresh writer-fenced
SQLite/source A/B backups, schema compatibility, power-loss replica
acceptance, and explicit owner approval. Telegram TEXT-to-PHOTO and
production source installation remain NO_GO.
