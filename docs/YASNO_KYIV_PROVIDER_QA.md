# YASNO Kyiv / DTEK group schedule — independent QA adapter

## Provenance

A public JSON endpoint at `app.yasno.ua` provides Kyiv schedule
groups. On 2026-11-08 a second public YASNO endpoint confirmed:

- Region 25: `Київ`.
- DSO 902: `ДТЕК КИЇВСЬКІ ЕЛЕКТРОМЕРЕЖІ`.
- The daily schedule endpoint:
  `/api/blackout-service/public/shutdowns/regions/25/dsos/902/planned-outages`.

This is **a directly fetched YASNO publication for the DTEK Kyiv
network**, rather than the protected `dtek-kem.com.ua` web page.
The latter still returns an anti-bot challenge from our host.
Do not claim DTEK itself endorses an independent API integration.

## Safe behavior

The standalone `yasno_schedule.py` fetches **only** the pinned
public HTTPS endpoint, with redirects blocked, a 15-second
timeout, a 1 MiB maximum, expected JSON content type and no
credentials. It makes no writes to Shelly, Telegram or production
SQLite. It does not run in the deployed bot.

It converts exact 30-minute intervals to a 48-character forecast:
`NotPlanned` to `O` (no planned outage), `Definite` to
`X` (planned outage), and `Possible` to `?` (uncertain).
All other slot types fail closed. A schedule must cover the
entire Kyiv local calendar day without gaps or overlaps.

Only status `ScheduleApplies` is accepted. An unpublished
`WaitingForSchedule`, `EmergencyShutdowns`, old calendar
day, stale fetch (over 2 hours), old publication (over 36 hours),
ambiguous dates or incomplete slots are rejected.

The 2-hour clock is **successful retrieval age**, not merely
`updatedOn`: an unchanged published daily schedule remains
usable if it is fetched again, within the 36-hour publication
window. The timestamp from an offline file's modification time
is not proof of network authenticity.

## QA commands (no Telegram messages)

```bash
python3 yasno_schedule.py --help
python3 yasno_schedule.py --fetch --group 10.1
python3 yasno_schedule.py --file /tmp/shelly-yasno-kyiv-20261108-readonly.json --group 10.1
PYTHONPATH=.:tests python3 -m unittest tests/test_yasno_schedule.py -v
```

The source was observed returning 60 groups on 2026-11-08,
including GPV10.1 with definitive planned windows 04:00–11:30
and 14:30–22:00. These are **forecast windows**, not physical
power states. Actual electricity remains measurable only by
fresh local Shelly telemetry. The current schedule is subject
to change.

Before integration, add an explicit operator-source label,
a cache with trusted retrieval metadata, group verification,
safe network retry/backoff, integration tests and recipient
review. No automatic production rollout is authorized.
