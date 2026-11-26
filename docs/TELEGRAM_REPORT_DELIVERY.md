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
