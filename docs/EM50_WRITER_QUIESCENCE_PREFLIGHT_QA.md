# EM-50 writer-quiescence release prerequisite (QA only)

A protected online SQLite snapshot can be internally consistent and
still become outdated immediately when Shelly polling adds another
measurement. Therefore "snapshot parity PASS" is not sufficient to
prove that later software rollout can safely replace live SQLite
history.

This read-only QA script checks a **necessary** prerequisite:
the known production Docker writer is stopped and the complete
measurement/outage histories plus Telegram status metadata are
unchanged during an observation window.

The script NEVER stops or restarts the bot, modifies SQLite, deploys a
container, sends Telegram messages, or approves a release.

## Commands

Safe no-op:

    python3 scripts/em50-writer-quiescence-preflight.py --help
    python3 scripts/em50-writer-quiescence-preflight.py --dry-run

Current LXC, expected FAIL because the production bot is running:

    python3 scripts/em50-writer-quiescence-preflight.py --check \
      --db /var/lib/shelly-voltage/voltage.sqlite3 \
      --container shelly-voltage-bot \
      --observe-seconds 15

The script uses Docker inspect .State (not environment data) to verify
the chosen producer process is not running before or after observation.
It then checks the source SQLite using mode=ro and the same full
measurement/outage row digests as the integrated parity gate.
The Telegram status ID is compared privately in memory and never
printed. Even an edit to an older measurement or outage is detected
if row counts do not change. The observation period may be 1–60s;
default is 15s. No sample content, tokens or user IDs are emitted.

## Safety limits and controlled commissioning

PASS only means this prerequisite is met during the observed window.
It is NOT proof that no other external writer exists, and it is NOT
approval to install or restore a database.

A user-approved release plan must identify and coordinate every
SQLite writer, stop the producer under a controlled outage-safe
procedure, preserve the latest history, create a protected versioned
snapshot, verify exact full-history parity immediately before
deployment, and obtain separate signed release/rollback authorization.
The real public Telegram status message must not be deleted or
rewritten merely to satisfy these checks.

Never stop the live Shelly monitor automatically as part of QA:
monitoring should remain active, especially during unreliable mains
power. A production stop/restart or modified systemd/Docker setup
requires an explicit operator decision and rollback preparation.

## Validation

11 isolated cases cover a currently running bot, automatic restart
during observation, new physical samples, historical row edits,
outage modifications, Telegram message ID rotation, missing status
metadata, zero-duration requests, disabled dry-run side effects and
nonsecret errors. All use synthetic disposable SQLite databases
and mocked Docker inspection; no real Shelly/Telegram operations.

    PYTHONPATH=.:tests python3 -W default -m unittest \
      tests/test_em50_writer_quiescence_preflight.py -v
    PYTHONPATH=.:tests python3 -W default -m unittest discover -s tests -q
    scripts/quality.sh
    bash tests/test_release.sh
