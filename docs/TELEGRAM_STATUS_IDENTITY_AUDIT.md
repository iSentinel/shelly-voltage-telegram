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
  verify --baseline /root/shelly-recovery-evidence/<checkpoint>.json \
  --output /root/shelly-recovery-evidence/verified-outage.json
```

Replace `<checkpoint>` with the real checkpoint filename and retain the
original immutable evidence. Output must not exist before the command;
the verifier writes this separate result once, with private permissions.
This is the **initial recovery check**: it
confirms the outage closed, new measurements resumed, and a genuine new
Telegram status was recorded. A scheduled utility forecast is **not**
independent evidence of restoration.

## Full 15-minute connection acceptance

Save a separate accepted verification checkpoint with `verify --output`.
After a full 15-minute sample window has elapsed since the exact outage
end, run the existing strict, read-only continuity checker:

```bash
python3 scripts/connection-recovery-window.py \
  --db /var/lib/shelly-voltage/voltage.sqlite3 \
  --baseline /root/shelly-recovery-evidence/<checkpoint>.json \
  --verification /root/shelly-recovery-evidence/verified-outage.json --json
```

**Important:** the connection recovery checker reports JSON
`"status": "pending"` with **exit code 0** while the 15-minute window is
incomplete. Exit code alone does **not** authorize physical acceptance:
only JSON `status == "pass"`, a matching incident, fresh continuous
phase samples with no gap beyond the configured threshold, online Shelly
and HTTP 200 readiness constitute success. On 2026-11-16, a real outage
#32 check at 7.38 elapsed minutes returned `pending` even though 44
valid L1 samples existed and the largest gap was 10.52 seconds. Do not
confuse this with PASS or a mandatory 15-minute Telegram post rotation:
the latter requires a distinct voltage-recovery event.

## Time-limited voltage-stability verification

A second acceptance command validates **stable voltage** after a recovered
voltage event. This is distinct from the completed 15-minute **connectivity**
window. Immediately after an authentic voltage recovery, use:

```bash
python3 scripts/recovery-acceptance.py \
  --db /var/lib/shelly-voltage/voltage.sqlite3 \
  stable-baseline --output /root/shelly-recovery-evidence/stable-baseline.json
```

After 15 uninterrupted minutes of normal voltage and automatic replacement
of the event-bearing Telegram status, persist an exclusive verification:

```bash
python3 scripts/recovery-acceptance.py \
  --db /var/lib/shelly-voltage/voltage.sqlite3 \
  stable-verify \
  --baseline /root/shelly-recovery-evidence/stable-baseline.json \
  --output /root/shelly-recovery-evidence/stable-verified.json
```

The implementation intentionally rejects **new evaluations more than one
hour after the original stable event**, with messages such as
`stable voltage transition evidence is outside one hour` and
`stable phase sample evidence is outside one hour`. This is a bounded
live-evidence retention rule, **not evidence that a previously verified
event has since failed**.

Archive the initial successful `stable-verify` JSON, exact SHA256, and
protected comparison copy immediately. Later acceptance should examine
that contemporaneous proof and independent Telegram evidence. Never
overwrite a saved PASS with a later expired live check, never relax
the hour-long limit to force PASS, and do not imply that a matching
same-host checksum is an independently protected off-host backup.

An actual voltage dip **resets** the 15-minute stable-voltage timer and
requires a new stable baseline. Do not reuse a superseded baseline or
mix its timestamp with the separate connectivity-recovery timer.
For outage #32 on 2026-11-16, connectivity recovery reached PASS after
15.96 minutes, while voltage events reset the stable timer from 17:13:40
to 17:21:06 Kyiv. The final stable verification passed at 17:36:40 Kyiv;
re-evaluation after 18:21 could correctly expire. The corresponding
public text status is Telegram post #1130, with an atomic ID/marker match.

## Channel posts from different publishers

A Telegram channel can contain independently published forecast alerts and
the single edit-in-place Shelly voltage status. **Do not treat the next public
post number as a duplicate voltage status without checking its content and
public author/signature.** The voltage-status ID stored in SQLite and its
atomic media binding identify the Shelly bot's current status, not every
subsequent channel message.

Real channel observation on 2026-11-16 (Europe/Kyiv):

| Post | Time (Kyiv) | Public author/signature | Purpose |
|---|---|---|---|
| [#1132](https://t.me/svitlobot_yordanska_7/1132) | Originally 20:02, edited later | `voltage monitoring` | Shelly live voltage status |
| [#1133](https://t.me/svitlobot_yordanska_7/1133) | 22:45:09 | `Світло ⚡️ Відключення` | 15-minute scheduled-outage forecast for 23:00–00:00 |
| [#1134](https://t.me/svitlobot_yordanska_7/1134) | 22:45:28 | `Світло ⚡️ Чат-бот` | Second forecast for the same 23:00–00:00 window |

These are two *separately signed* scheduled-forecast posts; the public
signatures alone **do not authenticate the underlying bot account or
identify which process submitted them**. Public HTML showed that
the Shelly voltage post #1132 remained intact and updated normally,
the DB status and text media marker both remained #1132, and no
status-publication pending flag was set. Therefore #1133/#1134 must
**not** trigger an emergency status-ID repair or be deleted by the
Shelly status cleanup code.

The two forecast notices are materially redundant; their owner(s) may
choose to coordinate notification policy after verifying the actual
channel publisher identities and whether both notifications are
intended. Do not suppress an unrelated forecast service from this
status-identity QA tool. A forecast is not a physical Shelly reading:
verify any claimed outage using live electrical telemetry, not merely
the existence of a scheduled-message post.

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
