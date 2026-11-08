# Natural Shelly recovery audit — source-only EM-50 QA

The installed 0.5.13-hw monitor was online again on
November 8, 2026 after its recorded L1 connectivity incident #18.
The incident began 06:47:05 and closed 11:55:01 (Europe/Kyiv),
a duration of approximately 5 h 7 min 55 sec.

This audit distinguishes **physical Shelly connectivity and stored
samples** from **Telegram-client delivery**. It does not claim a mains
power outage from connectivity alone. It does not alter the Shelly,
SQLite, Telegram, the running Docker container, or systemd.

## Run directly from the candidate QA tree

    python3 scripts/em50-natural-recovery-audit.py --help
    python3 scripts/em50-natural-recovery-audit.py --json

The tool uses the existing /var/lib/shelly-voltage/voltage.sqlite3
in SQLite mode=ro. The only HTTP requests are local /health and /ready,
with redirects, credentials, arbitrary paths and remote hosts rejected.

PASS requires all of the following conditions:

- SQLite integrity check OK, the latest outage has both start and end
  timestamps, and there are no concurrently open outages.
- No physical readings between the incident's start and the final
  60 seconds before closing. A limited pre-close allowance accounts
  for actual reconnect measurements during recovery hysteresis.
  The allowance is configurable within 0..120 seconds.
- At least one **real** nonzero measurement after the outage closed.
  No zero-voltage rows have appeared since this outage began.
- The **newest SQLite measurement for the recovering phase** must itself
  have a physically plausible voltage (0–500 V, exclusive) and frequency
  (40–70 Hz), a timestamp no older than 45 seconds, not in the future,
  and no more than 15 seconds before incident closure. A fresh HTTP
  health status or another phase cannot hide stale/corrupt phase data.
- Shelly currently online with /ready=true, a fresh successful poll
  no more than 120 seconds old and a positive realistic voltage.
- The saved Telegram status-message ID exists, **without disclosing
  its value**. Existence alone is not a delivery receipt.

The current report deliberately includes:

    telegram_channel_edit_visible = NOT_VERIFIED
    private_receipt_not_revalidated = true
    release_authorized = false

No pre-outage Telegram message-ID baseline was captured for outage #18.
It is not possible to retroactively prove a particular channel
message was edited/rotated from the current message ID alone.
A previously accepted private administrator send/edit receipt is
separate from **channel** visibility. Client-side acceptance must not
be inferred from the connectivity evidence.

An isolated fixture covers stale data, open outages, synthesized zero
voltage, early outage-time readings, missing samples/status metadata,
invalid API destinations, redirects and impossible voltage readings.
The source-only candidate 0.5.14-hw remains unpublished until a
reviewed systemd account+reader mount and explicit release/rollback
authorization.

## QA regression

    PYTHONPATH=.:tests python3 -W default -m unittest tests/test_em50_natural_recovery_audit.py -v
    PYTHONPATH=.:tests python3 -W default -m unittest discover -s tests -q
    scripts/quality.sh

For private on-host evidence, capture JSON with a restricted umask
in /opt/shelly-voltage/validation. Do not post private database
contents, tokens or Telegram identifiers to GitHub.
