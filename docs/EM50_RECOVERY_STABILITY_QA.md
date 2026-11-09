# 15-minute real Shelly recovery stability QA

This read-only QA follows the natural outage audit; it verifies
continuous physical observations after a closed Shelly connectivity
incident, not public Telegram message delivery.

The command:

    python3 scripts/em50-recovery-stability.py --help
    python3 scripts/em50-recovery-stability.py --json

checks the current /health and /ready endpoints on localhost only
and opens the installed SQLite via URI mode=ro. Its common recovery
dependency is scripts/em50-natural-recovery-audit.py, which already
verifies the current Shelly connection, closed incident, absence of
synthetic zero samples and saved Telegram status-ID existence.

A PASS additionally requires a COMPLETE trailing 15-minute window
(900 seconds) after the latest outage ended, with at least 60
timestamped physical phase readings, a maximum permitted gap of
30 seconds including the start/end edges of the time window, and
valid positive voltage and frequency for every sample.

An incomplete 14-minute recovery must FAIL even when /ready is
already green. A single gap of more than the maximum or a zero,
boolean, NaN or impossible voltage/frequency must also FAIL.
Configurable limits can be increased only within intentionally
bounded QA ranges.

Actual LXC110 natural outage #18 ended at 11:55:01 Kyiv on
November 8, 2026. The live read-only stability report after
12:13 showed 89 real L1 measurements over the latest 900 seconds,
maximum sample spacing 11.02 seconds, voltage 203.4–212.1 V and
PASS. The precise live values may change between checks.

This audit always reports:

    telegram_channel_edit_visible = NOT_VERIFIED
    release_authorized = false

A previous private administrator Telegram receipt is not public
channel edit/rotation evidence. A stored message ID in SQLite
does not prove subscriber visibility. Current source candidate
0.5.14-hw remains unpublished with no production systemd service
or read-only YASNO Docker reader mount activated.

Regression:

    PYTHONPATH=.:tests python3 -W default -m unittest tests/test_em50_recovery_stability.py -v
    PYTHONPATH=.:tests python3 -W default -m unittest discover -s tests -q
    scripts/quality.sh

This tool does not send Telegram messages, alter Shelly settings,
restart the container or write to production SQLite.
