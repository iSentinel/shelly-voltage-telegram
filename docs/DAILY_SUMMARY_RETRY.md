# Daily summary delivery retry and periodic job isolation

A Telegram send attempt can fail during an internet outage even
while the Shelly and SQLite monitoring loop is healthy. Previously
the daily summary was attempted again on every polling cycle
until Telegram succeeded. It also prevented the weekly private
digest and SQLite maintenance from running on the same cycle.

Draft behavior:
- Retry the failed daily summary at most every **300 seconds**
  of monotonic process uptime; do not depend on clock changes
  or a speculative DTEK outage schedule.
- Mark daily_summary_day only after Telegram has returned a
  successful sendMessage result. Never mark failed attempts
  as sent; distinguish this application acknowledgement from
  proof the subscriber actually saw the message.
- Run daily, weekly and database periodic jobs independently,
  logging only the exception type instead of error content
  (which could contain chat or service metadata).
- No new Telegram messages are sent by the test suite. Tests use
  mocks and temporary SQLite databases. This is only a Draft
  change: production remains on 0.5.13-hw pending hardware QA.

This intentionally throttles unsuccessful sends rather than
promising strict exactly-once delivery across a process crash
between a Telegram acknowledgement and SQLite metadata write.
