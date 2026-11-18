# Graphical status card

Use `/graphic_preview` in the administrator's private chat to review the
same card used for channel status. It shows connection, fresh voltage
and voltage severity separately. Pro EM-50 shows `L`. Pro 3EM shows
`L1/L2/L3` with an independent status for each phase. The active Telegram
language and warning and critical thresholds apply to the card.

Only a verified current YASNO forecast appears in the schedule. It does
not determine the measured electrical state. A stale or unavailable
reading shows a state label in place of a live voltage.

Before enabling channel graphics, validate the exact signed release,
review the private preview and run the status migration and rollback
checks with a fresh backup. The existing text marker must match the
configured channel and message ID, with no pending publication. A verified
text status can then be converted in place during the cutover. Verify the
photo and message ID in Telegram after activation.

The public status card normally has no caption. If a card cannot be
created or its upload is explicitly rejected, the bot can publish the
status as text. If a known photo cannot be updated, its caption carries
the fallback until the next successful photo edit. An unconfirmed new
upload blocks further posts until the result is reconciled.

Disabling graphics on a verified photo switches to caption updates.
Returning the channel to a text post requires a separate publication with
a verified destination and cleanup of the photo.

## Elapsed schedule times

The current Kyiv-local clock determines which YASNO schedule labels have
fully elapsed. Completed hourly labels in the 24-hour grid and fully
finished text windows are dimmed and struck through. The current
part-hour, its active schedule window and all future labels remain clear,
including at exact 30-minute boundaries and before midnight.

This presentation-only rule applies to both the smooth v5 card and the
bitmap v4 fallback. It never modifies the YASNO forecast, electricity
measurements, Telegram message identity or delivery state. A planned
power-on interval is not proof of measured power availability.

Network-isolated acceptance uses the test_elapsed_schedule_strikes.py
suite. It checks 03:59 versus 04:00, 16:45, 23:59 and 00:00 Kyiv time.
The existing renderer regressions verify schedule colors and current-slot
highlighting remain unchanged.
