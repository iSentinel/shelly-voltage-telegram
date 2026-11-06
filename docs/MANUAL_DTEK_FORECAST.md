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
