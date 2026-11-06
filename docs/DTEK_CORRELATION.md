# DTEK vs Shelly timing correlation (offline-only draft)

This module compares **when** the meter became unavailable (or a
separately confirmed electrical event happened) with the published
DTEK planned schedule. It never concludes **why** power was lost.

Outputs: start_within_window, partial_overlap,
outside_published_windows, and unknown.
The boolean cause_verified is always False.

Inputs require an explicit Europe/Kyiv schedule date and validated
half-hour outage windows. Missing provider data means unknown,
whereas an explicitly published complete day without outage
windows may legitimately yield outside_published_windows.

An ongoing event needs an explicit observation end timestamp to avoid
unbounded intervals or quietly assuming the current time. Historical
events cannot be matched against a graph from a different day.
Malformed or overlapping provider periods, invalid timestamps and
ambiguous fall-back DST half-hours fail closed to unknown.

**Important:** Shelly unreachable does not prove mains power is off.
Router, LAN, Wi-Fi and hardware faults can be responsible. Presentation
must describe such events as communication loss unless additional
electrical evidence is available.

This module has no IO, network, Telegram, SQLite or device operations.
It is a library candidate only. Future acceptance requires dated
schedule snapshots, source provenance, real device event checks and
operator-approved Telegram copy.
