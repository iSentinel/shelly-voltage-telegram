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
