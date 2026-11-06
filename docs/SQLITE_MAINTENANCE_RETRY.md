# SQLite maintenance retry safety during long Shelly outages

The monitor periodically rolls up historical samples, checkpoints
SQLite WAL and may VACUUM when there is sufficient free space.
A failed maintenance operation previously left the last-success
timestamp unchanged, causing **every polling cycle** to attempt
the same disk work until recovery. This risks unnecessary I/O and
log volume when the database is temporarily busy or storage is low.

The candidate now records the last maintenance **attempt** before
starting a rollup and permits another attempt only after 300 seconds
of monotonic uptime. Last successful maintenance retains its existing
24-hour schedule. An unsuccessful attempt does not mark success.
A later successful attempt restores the ordinary daily cadence.

This guard works with Draft #160's periodic-task isolation: a
maintenance exception can be caught without suppressing daily
or weekly Telegram checks, while repeated database retries stay
bounded. A process restart resets the in-memory retry delay;
this is not a durable backoff across crashes.

Tests use a disposable monitor and mocked storage. There is no
production SQLite write, Telegram request, Shelly control,
release publication or service restart.
