# Prometheus: connectivity loss is not proven mains failure

The previously exported shelly_outages_total,
shelly_outages_today and shelly_outage_duration_today_seconds
measure **Shelly polling connectivity losses**, not verified
electricity outages or exact physical switching times.

The exporter now emits explicit, equivalent metrics:
- shelly_connectivity_losses_total — cumulative transitions
- shelly_connectivity_losses_today — daily observed gaps
- shelly_connectivity_loss_duration_today_seconds — gap seconds

The cumulative metric is also available with a phase label
for configured Pro EM-50 / Pro 3EM / other supported phases.

The legacy series remain untouched as compatibility aliases;
their HELP text now clearly explains the historical name.
Existing dashboards can migrate gradually. The new metrics
must **not** be interpreted as measured power-off duration.
Prometheus graphs reflect connectivity evidence only.

In RU/UK/EN the empty Telegram event list no longer uses a
misleading "confirmed events" label. A user-confirmed electricity
outage remains separate independent evidence.

No network/device request, database migration, Telegram message
or production update occurs in the software QA change.
