# Issue #402 — read-only Telegram unknown-send intent triage

The current source reserves a durable SQLite `send:<random token>`
**before** calling the Telegram Bot API. If the Bot API accepted a post
but recording its returned ID fails, the token remains. That state is
deliberately **ambiguous**: automatic retry risks duplicate public
messages. Never guess whether Telegram accepted the remote send.

This tool **cannot** fix or authorize a failed publication. It is a
read-only, local-SQLite explanation of the channel's saved status.

## Command

```bash
python3 scripts/em50-telegram-send-intent-triage.py \
  --db /var/lib/shelly-voltage/voltage.sqlite3 --jsonl
```

Exit codes: **0 CLEAR**, **1 HOLD**, **2 ERROR** (missing, invalid,
unreadable or corrupted database). The output never includes the
pending `send:` secret token, Telegram token, or private chat ID.
`recipient_visible_delivery_verified` is always false because
SQLite metadata cannot prove receipt in the Telegram application.

The script reads one SQLite transaction with `mode=ro` and
`PRAGMA query_only=ON`. It never sends/edits Telegram messages,
removes a pending flag, changes a saved message ID, releases a
publication hold or authorizes a version or PHOTO cutover.

## Operator interpretation

- **CLEAR / ok:** no pending marker and a consistent saved status/media
  identity. Not evidence that the remote Telegram client displayed
  the saved message.
- **HOLD / unknown_remote_send_outcome:** a valid `send:` reservation
  remains. The remote Telegram attempt may have succeeded before
  SQLite failed. **Do not retry or delete the marker automatically.**
  The sole owner must check the recipient-visible channel and obtain
  the exact message/chat identity independently. Preserve the SQLite
  state and capture a WAL-consistent local backup. Any manual
  reconciliation requires a separate owner-approved, single-writer,
  transaction-safe procedure and exact evidence.
- **HOLD / invalid_send_intent:** malformed `send:` value. Keep the
  hold; inspect the durable state without exposing credentials.
- **HOLD / publication_pending:** legacy text confirmation, graphic
  publication, or recovery rotation is still pending. Do not
  automatically promote PHOTO or create another text post.
- **HOLD / saved_identity_invalid:** missing/mismatched stored message
  ID and media marker. Do not infer a replacement ID.
- **HOLD / sqlite_integrity:** SQLite consistency check failed;
  collect forensic evidence before any mutation.
- **ERROR / database_unreadable:** read-only open or query failed;
  fix source access/permissions through a separately approved
  operation, not by creating an empty production database.

The tool does not have a write, `--clear`, `--execute`,
`--retry`, or promotion command. Confirming a `send:` marker
without observing the corresponding remote message can cause a
duplicate or orphan; **automatic unknown-outcome recovery is unsafe**.

## Synthetic acceptance

```bash
python3 -m unittest discover -s tests \
  -p test_em50_telegram_send_intent_triage.py -q
```

Use a networkless, read-only container; run as both root and nobody
against disposable SQLite. Never point tests at live SQLite or
real Telegram credentials.

## Release context

One NVMe and sole-owner source review are already approved project
governance decisions. Neither proves remote Telegram delivery.
An owner approval for the exact version/tag, immutable signed OCI,
fresh local predeploy snapshot+offline restore, consistent
Compose/updater source identity, one-writer activation and separately
authorized PHOTO promotion remain independent requirements.
