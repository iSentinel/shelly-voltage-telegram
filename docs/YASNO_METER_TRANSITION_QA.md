# Verified YASNO vs. physical Shelly boundary audit

The scripts/yasno-meter-transition-audit.py CLI is a **read-only**
diagnostic. It compares the trusted Kyiv/DTEK YASNO 10.1 schedule
with actual Shelly EM-50 measurements already stored in SQLite.
It does not edit Telegram, send requests to Shelly, stop containers,
change settings, or write evidence files.

## Usage

Run from the candidate repository with Python dependencies available:

~~~bash
python scripts/yasno-meter-transition-audit.py --help

# Already observed on 12 November: physical samples around 04:00.
python scripts/yasno-meter-transition-audit.py \
  --database /var/lib/shelly-voltage/voltage.sqlite3 \
  --yasno-cache /var/lib/shelly-yasno-reader/yasno.json \
  --boundary 2026-11-12T04:00:00+02:00 --expect X:O --json

# Future scheduled edge: PENDING until 08:45 in Kyiv.
python scripts/yasno-meter-transition-audit.py \
  --database /var/lib/shelly-voltage/voltage.sqlite3 \
  --yasno-cache /var/lib/shelly-yasno-reader/yasno.json \
  --boundary 2026-11-12T08:30:00+02:00 --expect O:X --json
~~~

The boundary must be on the *current Kyiv calendar day*, carry the
correct UTC offset for Kyiv, and be aligned to a half-hour slot.
The cache must pass its provenance checksum and freshness checks.
A stale, missing or mismatched forecast returns FAIL, never a
fabricated outage or schedule.

PENDING (exit 2) means the requested real observation window is
not finished. By default the CLI observes 15 minutes before and
15 minutes after the schedule change. PASS (exit 0) requires at
least 60 measurements on **each** side, no missing span above 30
seconds (including window boundaries), positive finite numeric
voltage/frequency, unique timestamps, and no recorded outage
overlapping either side. Any violation gives FAIL (exit 2).

Measurements and overlapping outage records are read from one
consistent SQLite read transaction, even when the production writer
commits new data concurrently. The audit opens SQLite in read-only
mode and never holds a write lock or changes stored data.

This report distinguishes a **forecasted change** from what the
meter measured. It does not assert that YASNO caused an outage or
a restoration, and it does not authorize a production release.
Public graphical migration still requires a stopped writer,
proper source/SQLite A/B snapshots, exact Telegram media identity,
and recipient-visible acceptance.

To avoid disturbing production polling, run only this narrow
read-only audit on the live LXC. Run the full test suite and image
rendering regression on an isolated QA runner or GitHub CI.
