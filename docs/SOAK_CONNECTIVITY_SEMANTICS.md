# Soak report connectivity semantics (backward compatible)

The SQLite table named outages stores intervals during which Shelly
was not reachable by the polling service. Connectivity loss can arise
from mains loss, local network faults, router trouble or device errors.

**A connection-loss interval is NOT a confirmed physical mains outage.**

The report now exposes correctly labeled JSON keys:

- connectivity_losses
- open_connectivity_losses
- connectivity_loss_seconds
- connectivity_loss_intervals
- connectivity_loss_semantics

Historical confirmed_outages, confirmed_open_outages,
confirmed_outage_seconds, confirmed_outage_intervals, and
confirmed_outage_semantics are **deprecated compatibility aliases**.
They retain identical values during migration, while
legacy_confirmed_outage_keys_deprecated=true warns API consumers.

The plain-text CLI defaults to showing the accurately named fields,
not the legacy names. Aggregate voltages are actual observations
within the selected window, not a guarantee of continuous service.
An open connectivity interval is measured until the report time,
not the exact instant electricity physically returns.

No SQLite schema change, production deployment, Telegram command
or device-control operation is involved. Existing tests validate
identical legacy and new values across old and current schemas.

## Daily boundaries and future timestamp protection

The live monitor's daily connectivity loss totals are based on
the current Kyiv calendar day and the real observation timestamp.
When one network gap continues across midnight, it is still one
SQLite event; the next day's elapsed seconds begin at 00:00
instead of creating a synthetic closure and new event.

Daily stats now exclude future-starting gaps and clamp any
future recorded end timestamp to the actual observation time.
Neither forecast windows nor a bad clock value can inflate the
elapsed duration by adding seconds that have not happened yet.

The deterministic isolated tests exercise an incident starting
November 6 at 20:31:07 and continuing over November 7 00:00,
including future timestamp corruption. There are no physical
Shelly operations or writes to production SQLite.
