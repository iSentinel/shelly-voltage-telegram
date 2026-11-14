# Actual disposable systemd B/A start and ExecReload acceptance

**Issue #340. Only unprivileged networkless LXC114, not production.**

## Motivation

A previous real networkless LXC114 experiment confirmed that
a supervisor holding B SH can conflict with an ExecStartPre that
takes A EX when the two locks are incorrectly unified into one
inode, or are acquired in a circular order.

Production Compose can start a writer from *both* ExecStart
and ExecReload. Any global maintenance policy has to fence both.
Also manually invoked updater and boot recovery use A mutex,
whereas future guarded supervisor must coordinate a separate B
maintenance lock. No actual production release integration is
approved.

## This QA-only systemd model

The script is **inert by default**, and never accepts an arbitrary
target host, service name or data file. Explicit invocation checks
root, host name shelly-docker-reboot-qa, loopback-only network,
both original QA services disabled, and the standard QA Storage
Docker container stopped with policy=no and network=none.

The test creates a random unit whose name always begins with
svt-qa-ba-owner- and resides temporarily under
/run/systemd/system. A private 0700 temporary directory contains
two distinct 0600 locks B (maintenance) and A (updater), an
authorization marker, a small synthetic event-only runner,
and a record containing only start or reload. No SQLite is
mounted or written, and no Docker start/stop is executed.

The QA unit uses actual systemd:
- ExecStartPre obtains exclusive lock A with a 2-second timeout
- ExecStart checks B SH nonblocking, then records start
- ExecReload checks B SH nonblocking, then records reload
- ExecStop is /usr/bin/true; it does not alter a writer

**Six verified real systemd scenarios on LXC114:**
1. Parent holds B EX; systemctl start fails, no event written.
2. Parent holds B SH, separate A available; real systemd
   prestart and start succeed, exactly one start event.
3. Parent holds B EX; systemctl reload fails, no reload recorded.
4. After B released, systemctl reload succeeds, one reload event.
5. Parent holds B SH and A EX; systemctl restart prestart
   times out and fails in bounded fashion, no second start.
6. After A released, systemctl start succeeds, second start event.

A finally block always stops/resets the synthetic service,
removes its unit and calls daemon-reload. The randomly named
private directory and both locks disappear after each run.

## Commands

Default (strict no-op):

    python3 scripts/disposable-systemd-ba-start-reload-qa.py --dry-run

Real *disposable* LXC114 integration:

    python3 scripts/disposable-systemd-ba-start-reload-qa.py \
      --run-disposable \
      --confirm ISOLATED_SYSTEMD_BA_START_RELOAD_ONLY \
      --jsonl

-h/--help supported. Exit codes 0 = dry run or all six QA
checks pass; 2 = exact confirmation missing, 3 = failed or
blocked QA test. A successful test does not assert production
fencing or authorize maintenance.

## Limits

This uses a **synthetic systemd unit** rather than actual
production Compose or the signed updater entry point. It proves
B/A advisory lock order and the separate treatment of ExecReload
under genuine systemd but cannot prove physical host power-loss
durability, global Docker restart-owner fencing, or metadata
atomicity. An external writer that ignores locks may still start.

Do not install, enable or leave this QA unit in production.
Real updater/recovery, installer, rollback, emergency restore
and signed whole-current SQLite+source A/B still require
independent proof and owner maintenance signoff. Telegram
pinned TEXT-to-PHOTO remains NO_GO.
