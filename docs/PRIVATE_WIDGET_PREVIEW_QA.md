# Private widget fallback command — QA only

New optional Telegram text command: `/widget_preview`.

This is not a deployed Telegram HTML widget transport. It is a
safe, administrator-only **private chat** preview of the future
message's text caption. It is never processed for ordinary users
or in the public monitoring channel. Delivery uses
`telegram.send(..., notify=False, chat_id=<admin>)`, only after
that administrator explicitly sends the command.

The command composes a current `monitor.state.snapshot()` into the
validated public-data format and then formats it through
`widget_fallback_text`. Stale or disconnected voltage is hidden.
No Shelly controls, database writes or other Telegram edits occur.

Optional **local cached unofficial forecast**, disabled by default:

```shell
COMMUNITY_OUTAGE_CACHE_FILE=/path/to/kyiv.json
COMMUNITY_OUTAGE_REGION=kyiv
COMMUNITY_OUTAGE_GROUP=10.1
```

All three settings are required; the file must be regular, under
512 KiB, today's Kyiv date, parser-success status, known schema and
updated within two hours. Failures omit the forecast rather than
promoting uncertain data into claimed power state. There are no
network fetches from this formatter.

Timestamp and units: fractional last-success timestamps are floored
rather than rounded into the future; EN captions use `V` and `Hz`,
RU/UK retain local abbreviations.

## Test without any network / Telegram writes

```bash
PYTHONPATH=.:tests python3 -m unittest tests/test_private_widget_preview.py -v
PYTHONPATH=.:tests python3 -m unittest discover -s tests -q
scripts/quality.sh
```

This QA-only change is dependent on PR #205 and is **not ready for
production** until live private recipient acceptance, supported
Telegram clients and explicit release approval.
