# Manual outage schedule: explicit unverified source

This optional parser accepts a pasted DTEK-style forecast for a
specified date and consumer group. Source is always
user_supplied_unverified. It does **not** fetch the provider, create
or overwrite official DTEK caches, send Telegram messages, access
Shelly or change settings.

Preview is available in RU/UK/EN with a prominent disclaimer:
manual source, **not verified by DTEK**, actual power status unknown.
The field provider_verified remains False and cannot be promoted
by the parser. Forecasted windows are not authoritative outage events.

The 2026-11-06 forecast for group 10.1 supplied by the operator was
00:00-05:30, 10:30-18:00, 20:00-24:00. It was saved as private QA
evidence at:
/opt/shelly-voltage/validation/2026-11-06-group-10.1-user-forecast.json
and not copied into an official cache or public Telegram channel.

Future integration requires separate explicit private-admin opt-in,
input sanitization, source labeling, date expiry and user acceptance.


## Independent, read-only forecast vs voltage check

This CLI compares the private user-provided forecast against the
latest SQLite voltage sample and reports **separate evidence**:

    python3 scripts/manual-forecast-check.py --help
    python3 scripts/manual-forecast-check.py --forecast /opt/shelly-voltage/validation/2026-11-06-group-10.1-user-forecast.json --db /var/lib/shelly-voltage/voltage.sqlite3 --json

- It marks a matching forecast interval as predicted only.
- A valid recent voltage is visible with measured age and frequency.
- A stale, missing, 0 V or invalid reading is UNKNOWN, never 0 V.
- A connectivity gap cannot prove a mains failure.
- The future 20:00 window never becomes active before its start;
  24:00 is the exclusive endpoint.
- A different calendar day does not inherit yesterday's schedule.
- The tool has no network traffic, Telegram writes or Shelly commands.

A source_verified flag set to true causes FAIL. This tool must not
be integrated into automatic public channel publishing without
private-admin review and source-specific consent.


## Connectivity race at the forecast boundary

A recently recorded voltage is **not** a current reading if Shelly
has already entered an open connectivity gap. The checker suppresses
the voltage and sample age whenever any open connectivity gap exists,
even if the preceding measured sample is younger than max-age-sec.
This prevents the 20:00 boundary from showing stale mains readings
after communication has just failed.


## Private human-readable summary

The read-only checker can render localized, terminal-only operator
summaries. No Telegram API requests are generated:

    python3 scripts/manual-forecast-check.py --forecast /opt/shelly-voltage/validation/2026-11-06-group-10.1-user-forecast.json --db /var/lib/shelly-voltage/voltage.sqlite3 --language uk

The title and footnote explicitly identify an unverified manual
source. A predicted scheduled window and a fresh voltage measurement
appear on separate lines. If communication is lost, the measured
voltage is omitted and marked unknown, even if the last sample was
just a few seconds old.


## Approximate timing, not a switching timetable

The operator has clarified that the DTEK planned outage windows can
differ significantly from reality. Window membership denotes **only
a scheduled interval on the provided forecast**, not an exact moment
when mains supply changes. There is deliberately no made-up plus/minus
tolerance or automatic outage trigger when crossing 20:00 or 24:00.
An outage can happen earlier, later, or not happen at all.

The JSON report explicitly sets forecast_is_approximate=true and
forecast_window_does_not_trigger_outage=true. RU/UK/EN human-readable
summaries label forecast windows *approximate* and state that real
timings may shift. Only recent valid Shelly voltage is displayed as
measured evidence; an open connectivity gap hides the last value.


## Measurement coverage inside an approximate scheduled window

The read-only CLI also records the count and minimum/maximum of
**valid observed voltage samples** within the user-supplied planned
window up to the observation time. Samples outside that window and
invalid 0 V placeholders are excluded.

These observations establish measured voltage at individual moments,
not uninterrupted electricity for the full window. Missing samples
do not prove the mains are off. The report includes
proves_continuous_supply=false, regardless of the count. The planned
window does not trigger any Shelly or Telegram operations.


## Manual provenance consistency

A user-supplied schedule may never promote itself to official DTEK
data. The loader rejects missing verification metadata and
contradictory source_verified/provider_verified flags, including
a false value beside a true value. Reported sample counts and the
no-continuous-supply caveat are localized in RU, UK and EN.

## Independently confirmed power-loss incident review

When a user explicitly reports that electricity is off, their report
is independent evidence. It must **not** be confused with the instant
when the monitor notices missing Shelly connectivity. The source of
the schedule remains unverified, and neither event proves the precise
physical mains switching time.

For incident #15 on 2026-11-06, an offline CLI accepts an existing
private operator-confirmation snapshot and the read-only production
SQLite database. It verifies that the snapshot references the same
real network-gap event, checks the last valid voltage sample, and
prints a private RU/UK/EN provenance report:

    python3 scripts/incident-evidence-review.py --help
    python3 scripts/incident-evidence-review.py --incident /opt/shelly-voltage/validation/2026-11-06-2033-user-confirmed-power-loss-incident15.json --db /var/lib/shelly-voltage/voltage.sqlite3 --language ru
    python3 scripts/incident-evidence-review.py --incident /opt/shelly-voltage/validation/2026-11-06-2033-user-confirmed-power-loss-incident15.json --db /var/lib/shelly-voltage/voltage.sqlite3 --json

The review leaves exact_power_off_time=null, marks any
scheduled-window match as timing-only, and always reports
cause_verified_by_schedule=false. A Shelly network recovery does
not silently become proof that the power is back. Corrupt, missing
or verified-claim evidence is rejected. Synthetic zero-voltage
samples are excluded. The tool uses no network, Telegram or
SQLite writes.


## Long-duration disconnection evidence

During an extended offline period, the incident review emits
connection_gap_state, connection_gap_elapsed_seconds and
last_sample_age_at_loss_seconds. These are based on Shelly
network reachability and actual stored sample timestamps, **not**
physical mains switching timestamps or a measured power-outage
duration. The physical outage duration and current voltage are
always omitted from historical incident review.

Operators may specify an offset-aware --at timestamp for repeatable
read-only comparisons. When an outage remains open, the private
RU/UK/EN summary explicitly says current voltage is unknown;
after a recorded network recovery, the report still does not
claim a live current voltage or verified power restoration.
