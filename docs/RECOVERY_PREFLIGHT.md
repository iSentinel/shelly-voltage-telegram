# Natural Shelly recovery preflight (read-only)

This independent command helps acceptance of a **naturally restored**
Shelly while the operator has confirmed an electrical outage.
It does not contact Shelly, Telegram, external providers or network
services and never writes to the source SQLite database.

    python3 scripts/recovery-preflight.py --help
    python3 scripts/recovery-preflight.py --baseline /opt/shelly-voltage/validation/natural-baselines/outage-15-20261106-open.json --db /var/lib/shelly-voltage/voltage.sqlite3 --json

**Outcomes:** PENDING (exit 0) when the real device remains offline
or no recent valid voltage sample exists; CANDIDATE (exit 0) when the
original connectivity event has closed and fresh valid voltage appears
near its recovery; BLOCKED (exit 1) for inconsistent baseline,
wrong event, synthetic zero voltage, missing SQLite or other bad data.

A CANDIDATE means **only** a preliminary readiness condition. It is
not proof of Telegram delivery, 15-minute status stabilization, or
an independently established physical mains restoration time.

The project remains on the existing production runtime until human
release review. For full acceptance use the existing
scripts/recovery-acceptance.py with --db, --health-url, --ready-url
and the original baseline. Never create a fake event, force an
electrical switch or auto-publish a Telegram message.
