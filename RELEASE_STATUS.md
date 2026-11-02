# Release status

## 0.5.4-hw

- Current public release: `0.5.4-hw`.
- Current production live checkpoint: `0.5.4-hw`.
- Hardware acceptance passed on a physical Shelly Pro EM-50.
- CI covers Python 3.11, 3.12 and 3.13.
- The multi-architecture GHCR image and source release are published.
- Public release artifacts exclude deployment `.env`, secrets, SQLite data,
  Telegram identifiers, Shelly credentials and local validation artifacts.

`0.5.4-hw` includes the maintainability audit, permanent PEP 8 and function-size
quality gates, installer upgrade fallback for legacy meter configuration, the
multi-phase data model, native Shelly Pro 3EM / Pro 3EM-3CT63 support, selectable
Shelly backends, charts, CSV export, quality analytics, outage recovery status
rotation, watchdog metrics and the local dashboard.

Physical three-phase acceptance remains pending until a Pro 3EM / Pro 3EM-3CT63
is installed.
