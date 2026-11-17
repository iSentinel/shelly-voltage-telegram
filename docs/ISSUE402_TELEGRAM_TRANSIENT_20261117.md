# Issue #402 — transient Telegram API recovery, 2026-11-17

## Field evidence

At 03:12:37 Kyiv, Telegram returned an API 429 rate limit with a
bounded 5-second retry. Between 03:12:44 and 03:12:49, the API
returned 502 to polling and status editing. At 03:13:15–03:13:19,
status editing and polling encountered ReadTimeout. A further
polling ReadTimeout was recorded at 04:45:41.

At 08:17:42 Kyiv, the Shelly bot remained healthy with zero Docker
restarts. Four Telegram failures were accumulated, zero consecutive
failures remained and polling had succeeded ~3.3 seconds earlier.
SQLite status ID matched the text-media identity and no pending
publications were stored.

This is evidence of transient transport/API trouble and subsequent
polling recovery, **not** proof of a recipient-visible status edit.

## Required safety invariants

- Retry a Telegram 429 response at most once and cap retry_after.
- Do not automatically replay a 502 or a ReadTimeout on an ambiguous
  send/edit request. The remote side might have already accepted it.
- An unsuccessful edit must preserve the stored status message ID and
  its text-media marker. It must not recreate a second status post.
- Redact untrusted Telegram descriptions and avoid leaking tokens,
  chat IDs or message contents in logs and QA artifacts.
- Keep errors in the health counter distinct from current consecutive
  failures. A historical nonzero counter is not ongoing unavailability.

## Source-only regression coverage

The local isolated QA branch adds five tests across
`test_telegram_response_safety.py` and
`test_text_status_identity_qa.py`:

1. A 502 on editMessageText is reported without automatic retry.
2. A ReadTimeout on sendMessage does not trigger replay.
3. A 429 followed by 502 triggers at most one bounded retry.
4. A failed text edit after 502 preserves stable message identity.
5. A timed-out edit preserves identity without recreating a post.

Existing API error redaction and message status tests remain active.
Run all three suites as root and UID nobody in disposable containers
with no network, read-only source/root, synthetic Telegram credentials,
and dropped capabilities. No public Telegram request is authorized.

## Release decision

Testing transport error invariants does not establish public-channel
delivery acceptance, a signed release, off-host backup or approval
to enable PHOTO. Production emergency image
`shelly-voltage:issue402-v2-20261116` must stay unchanged until
independent restored backup, official signed OCI/source and updater
alignment, writer fencing, rollback and Owner Approval are complete.
