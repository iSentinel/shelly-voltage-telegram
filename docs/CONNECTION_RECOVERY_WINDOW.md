# Natural connection recovery — 15-minute QA

This is a separate read-only acceptance check for a device that
reconnects after a real network/power disruption. It does not
depend on the special voltage-critical-to-recovered 15-minute
timer. When that voltage timer is not active, no stable baseline
may be fabricated.

The CLI requires the original accepted connectivity-loss baseline,
the officially accepted full recovery verification, the existing
SQLite database, and read-only local /health and /ready endpoints.

It verifies the exact event identity and phase, absence of newer
connectivity losses, SQLite integrity, and zero synthetic 0 V
rows. The 15-minute window begins at the actual recorded
connection restoration timestamp, not an approximate timetable.

In that window, physically plausible same-phase readings
must be at most 90 seconds apart; the current latest reading
must be no older than 45 seconds.

The result is PENDING before 900 real seconds, BLOCKED when any
gate fails, and PASS after 900 real seconds with intact evidence.
Text mode omits private identifiers. The --json flag is intended
for machine-checked QA artifacts. Exit status is 0 for PASS or
PENDING and 1 for BLOCKED.

Example:

    python3 scripts/connection-recovery-window.py       --db /var/lib/shelly-voltage/voltage.sqlite3       --baseline /opt/shelly-voltage/validation/natural-baselines/outage-15-20261106-open.json       --verification /opt/shelly-voltage/validation/natural-baselines/outage-15-20261106-recovery-check.json

The checker never edits the database, controls Shelly, or sends a
Telegram message. PASS means sampled connection stability, not
uninterrupted physical voltage between polls, actual subscriber
receipt of Telegram notifications, or authorization to release.

For usage, run python3 scripts/connection-recovery-window.py --help.
