# DTEK published outage schedule (optional)

Configure `DTEK_REGION` and `DTEK_GROUP` in the private deployment
`.env` to enable `/schedule`. Regions: `k` Kyiv, `kr` Kyiv oblast,
`dn` Dnipro oblast, `o` Odesa oblast, `d` Donetsk oblast.
Group is a public DTEK queue number, such as `1.2`, without `GPV`.

The command is read-only and does not edit the stable voltage status,
post to the public channel, or change power monitoring. It fetches
the regional DTEK shutdowns page with a ten-second timeout and displays
today's outage windows at 30-minute resolution. Missing, invalid, stale,
or structurally changed data are reported as unavailable, never as
'no outages'. No address is sent to DTEK; only the public group is used.

Inspired by:
- https://github.com/mr-devboy/dtek-schedule (MIT)
- https://github.com/antondziuin/DtekSchedulePowerOutSenderBotHelper

This parser intentionally avoids browser automation inside production.
The upstream page may require JavaScript rendering; if so, the provider
fails closed until an independently maintained, verified data adapter is
available. Validate against current DTEK HTML before enabling in production.
