# Telegram Graphic Status — Design Polish v4

This is a source-only improvement to the 960×800 PNG renderer.
It does not alter Shelly polling, physical outage detection, SQL
state, YASNO schedule data, Telegram sending, or public opt-in.

## Requirements and acceptance

1. The Cyrillic voltage unit В shares the baseline with the
   primary measured voltage. Its green accent and the visual gap
   remain separate from the digits. Offline/stale cards must still
   hide historical readings.
2. Primary voltage uses smooth rounded vector strokes with 2×2
   subpixel coverage, not enlarged 5×7 blocks. Secondary Latin,
   Ukrainian and time labels have softly filtered cached glyph
   edges. No image, TTF or runtime font dependency is required.
3. For a **verified current Kyiv calendar-day** YASNO schedule,
   every past 30-minute interval is dimmed with a thin diagonal
   strike; the current interval is highlighted with a discreet
   amber outline; future intervals remain unmarked.
4. Half-hour states are calculated from the Kyiv local time
   used for rendering; they never come from meter connectivity.
   Mixed half-hour tiles are distinguished individually.
5. Forecast states O, X and ? retain their semantic
   colors. Crossing out a past forecast never means a recorded
   physical outage and never changes daily totals.
6. If the forecast is invalid, stale, unverified or from a
   different day, the grid stays unavailable rather than
   retaining yesterday's visual progress.
7. Image stays 960×800, private preview first, public rollout
   blocked until signed release and guarded A/B/media checks.

## Local smoke tests

~~~bash
python -m unittest discover -s tests -p test_graphic_polish_v4.py -q
python -m unittest discover -s tests -p test_telegram_card_png.py -q
python -m unittest discover -s tests -p test_graphic_nov12_real_schedule.py -q
scripts/quality.sh
~~~

Inspect a preview for correct baseline alignment, legible
antialiasing and completed/current/future half-hours. Rendering
and test fixtures must not contact Telegram or stop the writer.

## Release safety

PR #300 remains a draft candidate. Keep the published
RELEASE_VERSION at 0.5.15-hw until a separate signed release
promotion. A privately delivered preview is not authorization
to edit the public channel post. Exact Telegram media identity,
real writer-stopped A/B snapshots and recipient-visible
acceptance remain mandatory.
