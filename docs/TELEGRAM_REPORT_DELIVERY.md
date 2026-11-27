# Scheduled Telegram report delivery

Daily summaries and subscribed weekly reports run in a separate thread.
They use an isolated HTTP subprocess so a slow report can finish while
Shelly sampling and the usual Telegram connection continue. Several sampling
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

## Active request deadline

Each scheduled HTTP attempt now uses a separate subprocess, with a
12-second wall-clock budget including process startup. The parent polls
for STOP at most every 100 ms while waiting and kills/reaps the isolated
network worker on cancellation or timeout. The worker does not access
SQLite; its request is supplied through stdin rather than command-line
arguments. Its stderr is suppressed and stdout contains only validated,
bounded response metadata. The response body is limited to 64 KiB;
redirects are disabled. An explicit 4xx rejection must agree with the
HTTP status; a successful response must contain a positive integer ID.
Ambiguous responses leave the exact durable reservation pending.

The existing interruptible 429 delay is separate from the per-request
deadline. Ordinary alert/status and long-polling transports are unchanged.
This does not promise a hard real-time process shutdown bound under
kernel stalls or when other monitor work is blocked. Cancellation cannot
undo a remote accepted message and does not authorize automatic resend.

QA on 2026-11-26: seven loopback HTTP transport tests passed. In networkless
LXC114, the derived image stopped during a streamed accepted response
in 266 ms (exit 0), preserved its pending attempt and SQLite, and a new
process using the same database did not resend. A separate active request
without STOP hit the wall-clock budget, left the main monitor running
and the pending intent intact, and had no surviving network child.
Temporary QA logging driver was set to json-file for log inspection.
No live Telegram traffic or signed release changes.

## Protected reconciliation rehearsal

The optional `--protected-qa` mode is still restricted to LXC114,
using the dedicated root-controlled `/var/lib/svt-report-reconcile-qa`
layout, not the production database or fence. It exercises the order:
signed-forward operator lock, updater operator lock, writer lease and
transaction gate. Operator locks must already exist as protected regular
inodes. The writer fence directory must be root controlled and the two
writer lock files must belong to UID 10001. Both pending-update marker
paths, including dangling links, block reconciliation. The SQLite
resolution uses synchronous=FULL.

Actual isolated QA on 2026-11-26 refused contention on each of the four
lock files and refused both pending markers before creating a backup.
After releasing them, a copy of the active-HTTP cancellation fixture
was reconciled with 215 measurement rows, original inode and unrelated
metadata preserved; the pending rescue and its SHA256 matched the audit.

This is a rehearsal of lock ordering, not production authorization.
Production must additionally verify the exact signed installed pair,
actual mount identity, stopped supervisor and every other writer,
protected live paths and the owner's approval of the exact evidence
and attempt. The current production release does not implement report
reservations. Do not deploy this helper as a repair for v0.5.27-hw.
