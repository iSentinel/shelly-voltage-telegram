# Issue #402 — Explicit graphic renderer selection in offline acceptance

**QA fixture-only fix, 17 November 2026.** There is no change to
production rendering, stored Telegram message identity, network
monitoring, voltage samples, or the configured public status.

## Problem

The official pinned OCI used for networkless QA has
`TELEGRAM_GRAPHIC_STYLE=v5` in its environment. The older
`tests/test_graphic_offline_end_to_end.py` explicitly instruments
`telegram_card_png.label()` and `draw_voltage_type()`, the bitmap
renderer **v4**, but did not select that rendering mode. As a result,
when invoked under the v5 image it called the smooth v5 renderer and
those v4-only instrumentation mocks observed no calls. Five of 14
tests failed on missing v4 label calls even though the underlying
card was successfully rendered by a different implementation.

This is test fixture configuration drift, not evidence of a
production Telegram renderer failure.

## Fix

The v4-specific test classes now pin `TELEGRAM_GRAPHIC_STYLE=v4`
using `mock.patch.dict("os.environ", ...)` started in
`setUp()` and restored by `addCleanup()`. The patch is scoped
to each test; it neither modifies the user's `.env` nor changes the
v5 renderer in `telegram_card_v5.py`.

Two more legacy v4 suites also pin their bitmap rendering mode: `test_graphic_polish_v4.py` and `test_telegram_card_png.py`. The latter asserts unfiltered RGB bitmap PNG bytes and cannot be evaluated using the modern v5 encoder; the original failures were fixture drift.

The separate `test_telegram_card_v5.py` already selects v5
explicitly and continues to test its Unicode fonts, online/offline
state, and visual metadata in its own environment.

## Verification

Networkless, read-only Docker tests under both root and nobody:

```bash
python3 -m unittest discover -s tests \
  -p test_graphic_offline_end_to_end.py -q
python3 -m unittest discover -s tests \
  -p test_telegram_card_v5.py -q
python3 -m unittest discover -s tests \
  -p test_connectivity_wording.py -q
python3 -m unittest discover -s tests \
  -p test_telegram_localization.py -q
```

Both graphics suites and the connectivity wordings pass. A
Shelly connectivity incident does not prove physical mains loss;
when offline, the status must never display old voltage as a
fresh reading. The earlier 21.1-second incident #34 was a
communication interruption, with one valid recovery-grace
measurement, not a confirmed electricity outage.

No official release, Git tag, signed OCI, production restart,
Telegram send/edit, PHOTO or updater operation is authorized
by this test-only fix.
