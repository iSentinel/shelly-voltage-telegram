# DTEK-style day key compatibility (QA only)

The DTEK schedule parser previously accepted date strings (ISO or local
formats). The community mirror exposes a source-shaped `fact` object where
`today` is a Unix-second integer and `data` keys contain the same timestamp
as a decimal string.

The adapter now accepts either date labels or Unix seconds **only** when
the numeric timestamp represents 00:00:00 local Europe/Kyiv.
Wrong-day values, non-midnight timestamps, duplicate keys for one day,
milliseconds, booleans and malformed values fail closed. Local DST
transitions are covered by unit tests.

This is format compatibility, **not official provider attestation**.
The mirror is not an authoritative source for Shelly actual voltage or
mains outages. No actual Shelly event is generated from forecasts.

Validation (with the QA Python environment):
`PYTHONPATH=.:tests python3 -m unittest tests/test_dtek_unix_day.py`

Independent direct DTEK capture, timestamp attribution, and validation
remain mandatory before release.
