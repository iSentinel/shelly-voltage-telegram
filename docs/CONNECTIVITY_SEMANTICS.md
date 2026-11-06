# Connection loss is not proof of mains failure

The monitor records Shelly polling availability transitions in its
historic SQLite outages table for backward compatibility. The table
name is internal; its events represent **connectivity gaps**, not
verified loss of electricity.

Natural incident #14 on 2026-11-06 lasted around 45 seconds
(19:09:26-19:10:11 Europe/Kyiv) and coincided with both Shelly
No route to host and Telegram transport errors. This incident
does **not** prove loss of mains. A device may be unreachable because
of LAN, router, Wi-Fi, ISP, network routing or device trouble.

This PR corrects misleading historical Telegram UI terminology
in RU/UK/EN: headings, empty history, daily connection-loss totals
and the Russian multi-phase sensor recovery text. It preserves
existing SQLite schema, alert thresholds, channel routing,
Telegram message IDs, data retention and device polling behavior.

Actual voltage warnings continue to require valid Shelly voltage
measurements. Never infer present voltage from cached readings while
offline. All changes are presentation-only; production deployment
requires QA and operator acceptance.


## Telegram transport uncertainty

When Telegram times out while sending an availability notification,
the server may have accepted the message even when its response was
lost. This is **indeterminate delivery**, not proof of rejection or
proof of delivery. The existing monitor does not automatically replay
that independent alert on every offline poll, avoiding an unbounded
duplicate-send loop. The editable stable status is a separate path
and can resume once transport is available.

Offline tests use injected ReadTimeout/ConnectionError failures,
no real network and no subscriber messages. A human recipient check
is still required to accept the Telegram-visible result.
