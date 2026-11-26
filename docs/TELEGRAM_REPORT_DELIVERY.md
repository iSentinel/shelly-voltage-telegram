# Scheduled Telegram report delivery

Daily summaries and subscribed weekly reports run in a separate thread.
They use their own HTTP session so a slow report can finish while Shelly
sampling and the usual Telegram connection continue. Several sampling
ticks during an active report produce one follow-up check for due work.

The reporting schedule, quiet hours, weekly opt-in and retry limits stay
in force. The monitor waits for an in-flight scheduled report before
releasing database ownership on shutdown.

On shutdown a scheduled report does not begin another request, and an
explicit HTTP 429 retry delay is interruptible. An accepted response
still records its delivery marker before the writer lease is released.
A request already in flight is not forcibly cancelled by this change.
The 20-second Docker stop deadline versus a slow HTTP response remains
a separate acceptance gate. Do not treat transport errors as confirmed
delivery or weaken the signed rollback guard.

QA on 2026-11-26: a network-disabled derived image reproduced Docker
exit 137 after 20 seconds during a simulated 30-second HTTP 429 delay.
The interruptible retry build passed the same scenario with exit 0,
no retry, no false daily delivery marker, and intact SQLite.

## Ambiguous delivery

A scheduled report reserves `telegram_report_pending:<delivery-key>`
in SQLite before its request. The key is scoped to daily delivery or
one weekly administrator. An acknowledged send commits its period and
clears the exact reservation in one transaction. A confirmed rejection
or cancellation before sending/retrying clears the reservation without
marking delivery. Transport failure, invalid response or abrupt process
exit leaves it reserved. The same stream, including later periods,
does not automatically resend while its outcome is unknown.

An unresolved reservation also blocks the new updater preflight.
The earlier signed releases do not understand this marker. Do not
activate a downgrade to them or delete the marker to pass preflight.
The existing strict signed rollback rule remains necessary, and a
reviewed operator reconciliation procedure is still required. Do not
claim exactly-once delivery: a reservation can exist even if the
remote server never accepted the request.

Networkless Docker QA: a real loopback HTTP server accepted a synthetic
report and streamed the acknowledgement slowly. Both original and
corrected images hit the 20-second Docker stop deadline (exit 137).
With the reservation, SQLite remained intact and the next process
did not repeat the accepted request. The restarted process stopped
normally. This protects against duplicate automatic sends in the
tested crash scenario; it does not bound the active HTTP request.
