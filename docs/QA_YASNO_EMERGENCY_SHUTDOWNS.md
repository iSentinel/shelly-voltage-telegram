# QA: authoritative YASNO emergency shutdown status

On November 20, 2026 at 08:49 EET a read-only GET from the existing
YASNO service account reported the exact status EmergencyShutdowns for
both dates of group 10.1, with zero slots, and for today's entries of
all 60 groups (source updatedOn 05:58:49 UTC). This was not an HTTP
outage. The installed 0.5.25-hw reader rejects this valid provider
status, logs a failure and leaves an earlier plan in the cache.

## Candidate behavior

- Validate the Kyiv date, source publication/capture age and group.
  Only the exact EmergencyShutdowns status with an empty slots array
  is an authoritative no-plan notice. Unknown states, malformed
  notices, WaitingForSchedule and transport errors still fail closed.
- Replace the active source cache with a bounded, checksum-protected
  emergency response; the successful refresh can trigger the normal
  read-only recipient handoff. Do not send a new Telegram message from
  the refresher. An old plan is not a current forecast; no synthetic
  intervals are created. An older same-day provider publication is
  rejected by both the source and recipient writers; restoring a plan
  after an emergency requires a strictly newer publication timestamp.
- Carry the explicit notice to channel text, private caption, PNG
  v4/v5, and the prototype HTML widget. Hide the timeline and do not
  fall back to a community plan while the valid official notice is
  present. English, Ukrainian and Russian captions are supported;
  PNG v4 retains its existing Ukrainian bitmap labels.
- The notice expires at the same capture-age limit or Kyiv day
  boundary. It never changes physical phase connectivity, voltage,
  frequency, outage history or Telegram writer/cleanup state.

The same sealed cache schema is used: the payload is the original
provider response. Its checksum is corruption detection, NOT a
cryptographic provider signature. This intentionally removes the old
plan from the active cache when a verified emergency supersedes it;
there is no archival copy of the old forecast in this implementation.
Consumers that require an actual plan continue to reject emergency
notices. A valid emergency record is not positive release acceptance
evidence for a planned timeline.

## Validation and release restrictions

New integration tests cover provider validation, localization,
source-to-recipient refresh/handoff, older/equal plan replay, recovery
on a newer plan, all-phase voltage, expiry/conflicting metadata and
actual PNG rendering in the two styles. Existing cache, handoff,
schedule, widget and graphic regression suites were executed offline
in LXC114 using a disposable, network-disabled QA Docker container.
HTML tests check size and static safety; no connected browser runtime
or public Telegram send was used.

Source code is QA-only until independent signed-pair validation,
Owner Approval and protected cutover. No production file, database,
cache, Telegram status or service is changed by this work, apart from
the explicit ROADMAP checkpoint. Do not use the emergency incident
to bypass the required signed release gates.

## Expired official emergency and private community fallback

If a checksum-verified cache contained a valid emergency at retrieval
and the notice belongs to the current Kyiv day, its expiration removes
the visible notice and all timelines. It does not silently reactivate an
unofficial plan in the private widget caption. Fresh physical readings
are independent. A newer independently validated official plan may
restore intervals. Historical suppression is scoped to that civil day;
ordinary stale forecasts retain the existing labelled community
fallback, and corrupt or legacy unsealed data cannot establish this
verified emergency history. Public graphic and channel forecasts do
not use the community fallback.

Six offline tests cover exact expiry, current notice, restored
official schedule, tomorrow, ordinary stale forecast and corrupt
checksum. No change to the sealed cache format or producer/handoff
permissions.
