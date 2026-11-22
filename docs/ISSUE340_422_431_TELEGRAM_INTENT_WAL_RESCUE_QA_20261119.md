# Issues 340/422/431: preserve Telegram send and cleanup intent during WAL rescue

Date: 2026-11-19, Europe/Kyiv
Status: isolated QA acceptance; no Telegram API call or production update.

## Real schema used

The monitoring app persists Telegram state in SQLite:
- meta.telegram_status_media_identity (PHOTO binding);
- meta.telegram_status_message_id (current known status message);
- meta.telegram_legacy_text_confirm_pending (reserved uncertain send);
- telegram_status_cleanup(chat_id,message_id,needs_unpin,retry_at),
  a durable queue for old status unpin/delete operations.

The tests use the application Storage.initialize() and
Storage.reserve_text_post(), then insert one synthetic cleanup row in
the actual table. They do NOT invent new columns, alter production
database schema or contact Telegram.

## New end-to-end QA evidence

On isolated LXC114, using a disconnected UID10001 Docker image:
- Seeded real database with a synthetic Telegram pending send and
  one root-free cleanup-queue row, PHOTO marker and L1/L2/L3 records.
- Reproduced four actual updater-process SIGKILL checkpoints:
  after pending marker, writer stop, newest WAL rescue and rescue
  receipt creation. The root-owned marker was retained throughout.
- Recovered the newest complete SQLite/WAL copy; its entire SQL
  dump matched the live offline database. Explicitly confirmed
  pending send token and the full status_cleanup row survived.
- Changed the queued needs_unpin value in only the rescue copy.
  The real recover-receipt flow returned a history mismatch and
  refused to create a replacement receipt while pending remained.
- Restored the original rescue copy; rescue-receipt reconstruction
  and full parity passed. Invalid L3 history and forged SHA were
  also refused without clearing the pending marker.
- The same offline QA writer resumed after a controlled marker
  clear. Measurements grew from one to two per phase; the PHOTO
  identity, Telegram pending send and queued cleanup operation
  remained present and unchanged.
- The paired source/OCI positive proof was explicitly MOCKED, and
  no real signed new release was installed or rolled back.
- The QA environment cleaned all disposable containers, pending
  markers and staged source/receipt files.

## Limits and next work

This proves synthetic pending/cleanup persistence in the current
SQLite format through crash recovery, not Telegram remote delivery
receipt or user-visible message identity after a real signed image
migration. A production release still requires a new signed
source+OCI pair and an authorized sole-owner release transaction,
including end-to-end real signed image change and safe rollback.

The existing production v0.5.25-hw service, legacy --recover hook,
SQLite/WAL, Telegram and Docker restart policy remain untouched.

## QA command

    bash tests/qa_paired_rescue_runtime.sh

Root-only on LXC114 host shelly-docker-reboot-qa, with no network.
