# YASNO controlled refresh: QA release-preparation gate

A guarded one-shot refresh command is ready. No timer or service has
been installed or enabled in production.

## Supported commands

From the source tree:

    python3 scripts/yasno-refresh.py --help

    # Read-only: does not create a lock or contact YASNO.
    python3 scripts/yasno-refresh.py --dry-run --cache /tmp/shelly-yasno-qa-private/yasno.json --group 10.1

    # Read-only: exit 0 only for a fresh, valid, sealed schedule.
    python3 scripts/yasno-refresh.py --status --cache /tmp/shelly-yasno-qa-private/yasno.json --group 10.1

    # Opt-in: refresh at most once when successful retrieval is >=15m old.
    python3 scripts/yasno-refresh.py --once --cache /tmp/shelly-yasno-qa-private/yasno.json --group 10.1 --min-interval-seconds 900

The command reports PASS/FAIL and stable exit status 0/1.
Normal fresh-cache result: PASS SKIPPED.
--once takes a nonblocking exclusive lock in the protected cache
directory, validates current data, and performs at most one public HTTPS
fetch per invocation. Errors and unpublished schedules retain the
previous cache. A subsequent timer run may retry; there is no in-process
retry loop. Minimum allowed interval: 300 seconds.

This is only a YASNO forecast. It cannot change Shelly's measured voltage
or generate actual power outages. Private Telegram preview reads a cache,
not the provider's network API.

## Optional systemd examples: not installed

Files are under deploy/systemd and end in .example so they cannot
be enabled directly by systemd without review.

- shelly-yasno-refresh.service.example
- shelly-yasno-refresh.timer.example

They illustrate a 15-minute schedule with randomized delay,
a dedicated service user, and a protected state directory.
Before installing, a reviewer must verify:

1. A dedicated shelly-yasno account exists with no login privileges.
2. /var/lib/shelly-yasno is owner-only, mode 0700 and no symlinks.
3. The bot can read the resulting cache through a reviewed identity
   or protected volume sharing mechanism. Never chmod 777.
4. Source files actually exist at /opt/shelly-voltage and the
   target runtime includes Python 3.11+ and standard libraries.
5. Provider conditions permit the proposed public API cadence.
6. A real Telegram recipient test and rollback are approved.

No live installer or systemd activation is part of this QA PR.

## Future install sequence, only after formal review

    sudo install -o shelly-yasno -g shelly-yasno -m 0700 -d /var/lib/shelly-yasno
    # Install reviewed .service and .timer files into /etc/systemd/system.
    sudo systemctl daemon-reload
    sudo systemctl start shelly-yasno-refresh.service
    sudo systemctl status shelly-yasno-refresh.service
    sudo systemctl enable --now shelly-yasno-refresh.timer
    sudo systemctl list-timers --all

## Rollback of scheduled refresh (future only)

    sudo systemctl disable --now shelly-yasno-refresh.timer

The rollback stops new fetches and does not erase the current cache.
Restoring a previously approved production bot version remains a
separate release gate.

## Tests and monitoring

    PYTHONPATH=.:tests python3 -m unittest tests/test_yasno_refresh.py -v
    PYTHONPATH=.:tests python3 -m unittest tests/test_yasno_refresh_service.py -v

Tests cover lock contention, invalid/stale cache, forbidden fast
intervals, transient provider errors and preserving the last-good
payload. A refresh failure does not alter Shelly telemetry. Clients
hide expired forecasts.

This feature does not make experimental Telegram HTML widgets
publishable to public channels via normal Bot API.


## Root-owned recipient handoff (optional, pending review)

The refresh service runs as shelly-yasno and cannot alter a
root-owned bot-group reader directory. Successful refreshes may
trigger an independent OnSuccess=shelly-yasno-handoff.service
oneshot that validates source integrity and freshness, then
atomically installs the recipient copy as root:10001 0640.
The protected recipient directory is root:10001 0750.
A read-only Docker bind exposes only that directory to UID10001.
These templates remain uninstalled; validate systemd permission
constraints inside the target LXC before activating.
