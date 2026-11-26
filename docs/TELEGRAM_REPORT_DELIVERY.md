# Scheduled Telegram report delivery

Daily summaries and subscribed weekly reports run in a separate thread.
They use their own HTTP session so a slow report can finish while Shelly
sampling and the usual Telegram connection continue. Several sampling
ticks during an active report produce one follow-up check for due work.

The reporting schedule, quiet hours, weekly opt-in and retry limits stay
in force. The monitor waits for an in-flight scheduled report before
releasing database ownership on shutdown.

On shutdown a scheduled report does not begin another request, and an
explicit HTTP 429 retry delay is interruptible. An accepted response
still records its delivery marker before the writer lease is released.
A request already in flight is not forcibly cancelled by this change.
The 20-second Docker stop deadline versus a slow HTTP response remains
a separate acceptance gate. Do not treat transport errors as confirmed
delivery or weaken the signed rollback guard.

QA on 2026-11-26: a network-disabled derived image reproduced Docker
exit 137 after 20 seconds during a simulated 30-second HTTP 429 delay.
The interruptible retry build passed the same scenario with exit 0,
no retry, no false daily delivery marker, and intact SQLite.

## Ambiguous delivery

A scheduled report reserves `telegram_report_pending:<delivery-key>`
in SQLite before its request. The key is scoped to daily delivery or
one weekly administrator. An acknowledged send commits its period and
clears the exact reservation in one transaction. A confirmed rejection
or cancellation before sending/retrying clears the reservation without
marking delivery. Transport failure, invalid response or abrupt process
exit leaves it reserved. The same stream, including later periods,
does not automatically resend while its outcome is unknown.

An unresolved reservation also blocks the new updater preflight.
The earlier signed releases do not understand this marker. Do not
activate a downgrade to them or delete the marker to pass preflight.
The existing strict signed rollback rule remains necessary, and a
reviewed operator reconciliation procedure is still required. Do not
claim exactly-once delivery: a reservation can exist even if the
remote server never accepted the request.

Networkless Docker QA: a real loopback HTTP server accepted a synthetic
report and streamed the acknowledgement slowly. Both original and
corrected images hit the 20-second Docker stop deadline (exit 137).
With the reservation, SQLite remained intact and the next process
did not repeat the accepted request. The restarted process stopped
normally. This protects against duplicate automatic sends in the
tested crash scenario; it does not bound the active HTTP request.

## Operator reconciliation prototype (QA only)

The separate script `scripts/reconcile-report-delivery.py` inspects one
exact daily or weekly report stream with `--inspect --database PATH
--stream KEY`. It returns a fingerprint bound to the SQLite inode,
pending period, unique logical attempt ID and prior delivery marker. No Telegram API is called.

Use `--apply-qa` only on isolated LXC114, with the database-local
writer fence, no active writer or pending update, a fresh inspection,
`--expect-fingerprint HASH`, `--outcome delivered|rejected`,
`--evidence-ref REF` and a new `--backup PATH` in a root-owned 0700
directory. Delivered requires the independently confirmed
`--message-id ID`; rejected requires positive rejection evidence,
not merely an absent response. Do not put secrets in evidence references.
The script records an operator attestation, not remote verification.

The rescue is created and checked before any database change.
Resolution and its audit record are committed together. The original
database inode and unrelated history remain in place. The earlier
pending reservation is retained in the rescue copy.

Production writes are not implemented or authorized. Before extending
this prototype to production, require the protected production writer and
updater fences and validate operator evidence and backup freshness.
Do not use a fingerprint from a previous attempt even if it has the
same period. No automatic clearing,
downgrade authorization or remote exactly-once guarantee is implied.

QA evidence 2026-11-26: stale fingerprint rejected; a copy of the actual
SIGKILL fixture was reconciled as synthetic delivered message 42.
All 11 measurement rows and other metadata matched the original;
the backup retained the pending intent and the audited SHA256 matched.

## Unique logical delivery attempt

New pending reservations store `period:attempt_id`, where the attempt
ID is a random 128-bit hexadecimal token. Runtime completion requires
both exact values. The operator inspection fingerprint includes the
attempt ID, and the resolution record uses the same ID. A new reservation
after an explicit rejection has a new identity even within the same
period. An explicit 429 retry within one reservation retains its identity.

Legacy period-only pending values remain blocking: the new reconciliation
tool refuses them rather than inventing an attempt identity or clearing
them. Production reconciliation remains unimplemented.

Networkless LXC114 QA verified stale runtime acknowledgement and stale
operator fingerprint rejection for a replacement attempt of the same
period. The current synthetic attempt was reconciled independently;
11 original measurement rows and the pending rescue copy were preserved.
