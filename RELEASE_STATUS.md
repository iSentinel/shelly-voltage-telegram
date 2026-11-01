# Release status

## 0.5.3-hw

- Public software candidate: `0.5.3-hw`
- Current production live checkpoint: `0.4.12-hw`
- CI must pass on Python 3.11, 3.12 and 3.13 before tagging the release.
- Container image workflow must pass before publishing a GitHub Release.
- Public repository contains no deployment `.env`, secrets, SQLite database, Telegram IDs, Shelly credentials or local validation artifacts.

The `0.5.3-hw` candidate includes the multi-phase data model, native Shelly Pro 3EM / Pro 3EM-3CT63 support, selectable Shelly backends in Telegram settings, charts, CSV export, quality analytics, outage recovery status rotation, watchdog/metrics and local dashboard.
