# Telegram status identity: read-only incident audit

This diagnostic is for the text-message identity mismatch tracked in #402.
It never sends, edits, pins, unpins, or deletes Telegram posts. It also does
not connect to Shelly or change application settings.

## Run on an authorized host

Read an existing SQLite database, including its current WAL snapshot:

```bash
python3 scripts/telegram-status-identity-audit.py \
  --db /var/lib/shelly-voltage/voltage.sqlite3

python3 scripts/telegram-status-identity-audit.py \
  --db /var/lib/shelly-voltage/voltage.sqlite3 --jsonl
```

Exit codes are stable: **0** means status ID and text/photo media marker
match and no publication is pending; **1** means missing/mismatched identity,
pending publication, or a SQLite integrity failure; **2** means the database
could not be read or the schema is invalid. Output intentionally excludes the
channel/chat ID and tokens. Post numbers are the existing public Telegram
message IDs; do not put private chat IDs, credentials, or SQLite contents in
issue comments.

The command uses SQLite `mode=ro` and `PRAGMA query_only=ON`. It verifies
metadata consistency; **it cannot establish actual Telegram delivery or
the freshness of electrical measurements**. Confirm the channel's public post
independently before any production reconciliation.

## What an electricity outage looks like

An offline Shelly can leave the Docker container **running and healthy** while
`/ready` correctly returns **HTTP 503**. The Telegram status should display
that current voltage is unavailable, not a stale cached voltage as live.
The ID audit may still return **PASS** during an outage if the bot's saved
Telegram message identity is consistent. Do not restart the container to
force a notification or create another bot instance.

For a naturally occurring outage, the existing acceptance CLI can capture a
private, once-only checkpoint without sending Telegram requests:

```bash
python3 scripts/recovery-acceptance.py \
  --db /var/lib/shelly-voltage/voltage.sqlite3 \
  capture-once --output-dir /root/shelly-recovery-evidence
```

The destination must be private (mode 0700); checkpoint files use mode 0600
and contain a fingerprint instead of the raw Telegram message ID. After
power truly returns and measurements resume, verify the same outage baseline:

```bash
python3 scripts/recovery-acceptance.py \
  --db /var/lib/shelly-voltage/voltage.sqlite3 \
  verify --baseline /root/shelly-recovery-evidence/<checkpoint>.json
```

Replace `<checkpoint>` with the real checkpoint filename and retain the
original immutable evidence. The verifier must see the exact outage closed,
fresh continuous meter telemetry and a genuine new Telegram status. A
scheduled utility forecast is **not** independent evidence of restoration.

## Rollout discipline

During an unresolved outage, avoid bot restarts, `docker compose up
--force-recreate`, ad hoc SQLite metadata clearing, manual Telegram sends,
and graphic PHOTO mode switches. An ID/format mismatch must fail closed until
the **actual public post** is independently verified; any controlled repair
must use backups, compare-and-set, and Owner Approval.

The QA tool is shipped as a Draft PR only. The live 0.5.21-hw-derived
emergency v2 image may differ from the `/opt/shelly-voltage` host checkout.
Never assume the host checkout is the running source. Backups located only
on one Proxmox NVMe share the same physical failure domain; an independently
protected, restored off-host copy is a separate release requirement.

## Validation

```bash
python3 -W default -m unittest discover -s tests \
  -p 'test_telegram_status_identity_audit.py' -v
/bin/bash scripts/quality.sh
```

The test suite uses disposable SQLite databases, tests both matching and
mismatched media bindings, and asserts that an absent database is not
created by the audit CLI.
