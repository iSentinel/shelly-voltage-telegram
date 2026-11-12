# Telegram administrator guide

This guide describes features implemented in the **source candidate
`0.5.13-hw`**. The installed `0.5.12-hw` bot does not yet contain
`/events`, `/week`, `/preview`, or the private view/subscription controls.
Do not assume these commands work until a release is explicitly promoted
and accepted with the real Shelly Pro EM-50.

## Access and privacy

The existing Telegram status channel remains a **read-only
presentation of real observations**. The bot does not change the Shelly
mains supply or control a relay. Never interpret a lost Shelly connection
as a measured 0 V or a verified power outage.

The commands below that change private preferences are accepted only
from the **direct message chat of a configured Telegram administrator**.
A command posted to the status channel must not subscribe anybody or
change a private preference. A manual `/week` query in the authorized
control chat is distinct from an opt-in scheduled subscription.

## Commands

| Command | Purpose | Delivery / side effects |
| --- | --- | --- |
| `/events` | Recent saved voltage transitions and Shelly connectivity changes | Read-only; limited by retained history |
| `/week` | Seven-day quality report | On demand; no subscription implied |
| `/preview` | Show the administrator's selected status layout | Silent reply to this admin's private chat only |
| /graphic_preview | Candidate v2 PNG and YASNO | Admin private photo only; NOT yet deployed |
| `/view compact` | Choose compact private status | Save per-admin preference; no channel edit |
| `/view detailed` | Choose full status and settings in private chat | Save per-admin preference; no channel edit |
| `/week subscribe` | Opt in to a private weekly digest | Save per-admin opt-in; disabled by default |
| `/week unsubscribe` | Stop the private weekly digest | Save per-admin opt-out |

The compact view is the default. Layout and subscription settings
survive a restart in SQLite. Changes are recorded in the existing
settings audit journal and are independent for each administrator.

### Weekly schedule and delivery

An opted-in administrator receives a **silent** private report on Sunday
at or after **20:00 in the configured server timezone** (currently
`Europe/Kyiv`). No weekly report is sent automatically until that
administrator opts in. The normal status channel, pin policy, edit
window, quiet hours, alert cooldown and separate daily summary remain
unchanged.

After a successful Telegram API send, the bot records an ISO week
marker in SQLite to prevent normal repeat deliveries after a restart.
A failed send is retried no more than once per hour within the current
process. This is *best-effort*, not guaranteed exactly-once delivery:
a crash between Telegram acceptance and saving the week marker can
result in a duplicate. If the bot was offline during the scheduled
window, delivery requires the process to run during the remaining
Sunday window.

### Interpreting a weekly report

The report shows recorded min/max/average voltage and sample count,
estimated time outside configured thresholds, measured coverage,
**unknown coverage**, and recorded connection-loss durations. An
interval between valid samples of more than 30 seconds contributes to
unknown time, not to estimated voltage time. A long period of unknown
coverage does **not** prove a long power outage.

## Safe acceptance after a future release

1. Confirm the production Docker image, source version, rollback
   archive and SQLite continuity; validate `/ready` using a fresh
   measurement from the physical device.
2. In the administrator's **direct chat**, test `/preview` and
   `/view detailed`, then return to `/view compact`.
3. Compare `/week` with database records; check that no zero-voltage
   samples were fabricated during connectivity loss.
4. Test `/week subscribe` and `/week unsubscribe` only with
   explicit permission. Confirm no channel edit, pin or extra
   notification occurred. Do not force a real send by changing the
   server clock or causing an artificial Shelly outage.
5. Check Telegram delivery visibly in the administrator chat.
   A stored Telegram message ID is not proof that a subscriber
   actually saw the message.

**Release guard:** never promote candidate `0.5.13-hw` on offline
Shelly or stale restoration evidence. Full natural Pro EM-50 hardware
acceptance, verified rollback and owner authorization remain required.
The Pro 3EM / 3CT63 physical acceptance is separate and must wait
until that meter is actually installed.
