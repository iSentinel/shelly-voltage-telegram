# Scheduled Telegram report delivery

Daily summaries and subscribed weekly reports run in a separate thread.
They use their own HTTP session so a slow report can finish while Shelly
sampling and the usual Telegram connection continue. Several sampling
ticks during an active report produce one follow-up check for due work.

The reporting schedule, quiet hours, weekly opt-in and retry limits stay
in force. The monitor waits for an in-flight scheduled report before
releasing database ownership on shutdown.
