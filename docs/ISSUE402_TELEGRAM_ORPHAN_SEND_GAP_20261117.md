# Issue #402 — durable text-send intent (QA-only prototype)

**Status: SOURCE QA ONLY / NOT DEPLOYED / NOT RELEASE AUTHORIZED.**

## Reproduction of the prior design gap

On 2026-11-17, with Shelly naturally offline in outage #33, an isolated
synthetic test confirmed a gap in the candidate's original status path:
Telegram could acknowledge a send before SQLite committed its ID.
A synthetic database write failure then left the new remote post
untracked. A subsequent initial-status update could send again.
These were fake Telegram calls and disposable SQLite files. **No real
duplicate Telegram post was observed.**

## Local, unmerged mitigation design

The source-only QA candidate persists a unique 128-bit random intent
in existing \`telegram_legacy_text_confirm_pending\` metadata **before**
calling Telegram. The insert fails closed if either a legacy or PHOTO
publication is already pending. All five existing text-status send paths
call one \`send_confirmed_text_post()\` helper.

When Telegram returns a confirmed ID, \`Storage.bind_text_post()\`
validates the exact intent and commits status ID, \`text:<chat>:<id>\`
media marker, and removal of that same intent in **one SQLite
transaction**. Failed transport or SQLite commit leaves the intent
durable, blocking another send even after a process restart.
The previous saved status ID and marker survive failed writes.

This is at-most-once **automatic** send attempt semantics while
publication outcome is unknown. It does **not** provide exactly-once
Telegram delivery. Remote send may have succeeded or failed.

## QA gates

- Simulated remote success followed by failed SQLite binding does not
  send again automatically.
- Simulated ReadTimeout persists uncertainty across restart.
- A marker-write failure rolls back ID changes and retains the intent.
- A pending PHOTO intent blocks text sends.
- Valid successful send clears only its own intent with ID+marker.
- Existing text, voltage, recovery rotation and stable-recovery paths
  remain covered by source AST checks and synthetic lifecycle tests.
- Never publish test messages: Docker QA runs network disabled, only
  mock Telegram and disposable SQLite are used.

## Operational reconciliation required

An unresolved pending intent needs an **explicit, audited operator
decision**. Verify the actual public status and Telegram message ID,
compare SQLite saved identity and channel, back up current state, and
decide whether to bind an independently confirmed post or abandon the
intent when no post was created. Do not clear the marker blindly or
automatically retry; an earlier remote delivery can be unknowable.
No live SQL modification or Telegram API call is authorized by this
document. Review recipient-visible behavior and rollback as separate
release gates.

The deployment has one NVMe. No second physical backup device will
be supplied. Local SQLite backup and restore checks remain useful,
but catastrophic single-disk loss is not covered or explicitly
accepted by this QA change.

At 2026-11-17 10:53 Kyiv the live text bot was healthy, zero restarts,
and /ready returned 503 while Shelly was offline. **Never deploy this
prototype during the open outage**. Independent code review, full
regression tests, signed OCI/source and owner release approval are
still pending.
