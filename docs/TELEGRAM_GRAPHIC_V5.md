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
