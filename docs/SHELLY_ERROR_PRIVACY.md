# Shelly read-error privacy

The monitored Shelly address may be a private IP, internal hostname,
or a URL containing credentials. Network exception strings from the
HTTP library commonly include the entire destination in diagnostics.
Do not serialize those strings into service state, `/health`,
`/status.json`, or JSON phase snapshots, or print them to logs that
may be exported in an operator support bundle.

The monitor now uses a small allowlist of fixed diagnostic categories
such as `timeout`, `connection_error`, `connection_refused`,
`http_error`, `invalid_response`, and `read_error`. The value is
determined by exception type, not arbitrary remote error text.
Unknown exceptions must default to `read_error` rather than revealing
untrusted content. This applies to L1 and optional L2/L3 phase pollers.

**Important:** This is only a code change in the QA integration
candidate until approved for production deployment. Existing
production logs and health responses may still contain unredacted
historical errors. Avoid publishing raw live support bundles until
the production release has been approved and privacy-reviewed.

This does not change offline thresholds, polling cadence, outage
history, status-card text, Telegram notification settings or current
mains measurements. Unit tests mock errors containing synthetic URLs
and secrets; no device or external API is contacted. User-facing
logs retain failure counters and a safe reason classification.
