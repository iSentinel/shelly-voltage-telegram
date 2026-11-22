# YASNO midnight rollover and honest unavailable schedule

Date: 2026-11-20, Europe/Kyiv
Scope: group 10.1, Kyiv, source-only QA; no production deploy.

## Live incident and recovery

At 2026-11-20 00:00:34 +02, the YASNO refresh failed with
"schedule date differs from today": the provider's "today" still
held the previous civil day after midnight. The bot consequently
rendered its no-forecast card label even though a schedule existed
for the new day. At 00:15:34 the refresh and handoff recovered.

At 01:24 +02, LXC110 confirmed:
- Docker healthy, restarts=0, /ready HTTP200 and no pending update;
- read-only YASNO producer/consumer cache for 2026-11-20, group 10.1;
- both caches fetched 01:01:34 +02 and held the same planned outages
  for 11:00-16:00 and 21:00-24:00 local Kyiv time;
- producer timer and reader handoff continued normally.

No production executable, SQLite, Telegram or Docker change was
needed for recovery. The owner reported that the schedule appeared.

## Isolated source fix

yasno_schedule.current_day_bars checks the current day's published
YASNO "today" record first. Only if its date differs from today's
Kyiv date, it verifies the same "today" record as exactly yesterday,
including a valid published status and full slot coverage, and
then uses "tomorrow" ONLY if it contains an actually published,
valid schedule for the current Kyiv civil day.

No graph is fabricated when tomorrow is unpublished or stale.
Two-day-old provider records, invalid slot schemas, missing
publication status or mismatched dates remain rejected.

The Design Polish v5 no-forecast caption in UK/RU/EN now says
the schedule is temporarily unavailable rather than implying
the outage schedule itself does not exist:
- uk: Графік тимчасово недоступний
- ru: График временно недоступен
- en: Schedule temporarily unavailable

## QA evidence

LXC114 offline networkless Docker with no Telegram credentials:
137/137 YASNO, cache, handoff, channel, widget, PNG and midnight
rollover tests PASS. Ruff0.14.2, source maintainability and
formatting checks PASS. No live cutover executed.

The tests verify acceptance of published next-day slots only
during one-day rollover, rejection of waiting/old/malformed
records, and truthful no-forecast labels.

## Release gate

Changes are QA source-only; they are NOT active on the current
production v0.5.25-hw until a real complete signed source+OCI
release passes exact SHA, public signature, single-writer
cutover and newest SQLite/WAL/PHOTO/Telegram rollback gates.

Do not update LXC110 through the legacy automatic --recover
hook or restart the healthy bot just to deploy the caption.
