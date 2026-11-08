# Read-only private Telegram client acceptance evidence gate

The operator has already visually confirmed the one previously approved
private Telegram send/edit test. The 0.5.14-hw prerelease preflight
formerly reported that human gate as PENDING even though the private
client showed the edited message. This new optional receipt reader
keeps manual evidence distinct from automatic tests and release approval.

No new Telegram messages are sent and the public channel is never
edited by this tool.

## Read-only preflight command

After local evidence is recorded (file stays on the private server):

    python3 scripts/em50-release-preflight.py --help

    python3 scripts/em50-release-preflight.py --json \
      --acceptance-evidence /opt/shelly-voltage/validation/em50-telegram-private-client-acceptance-20261108.json \
      --cache /tmp/shelly-yasno-handoff-20261108/yasno.json

The optional --acceptance-evidence file MUST be a single, normal file,
owned by the preflight executor, mode exactly 0600; neither it nor its
parent paths may be symlinks. The local JSON must show Telegram
sendMessage PASS, editMessageText PASS, and actual end-user-visible
edited-message PASS, with an explicitly private administrator chat.
The recorded operation must indicate public_channel_modified=false,
pinned_message_modified=false, and released=false. The confirmation
timestamp must include a timezone and be no older than 30 days.

An absent or invalid report **never** silently marks any human gate as
done. The CLI only prints pass/fail state, never raw proof contents,
Telegram user IDs, chat IDs or tokens.

The result now contains:
- manual_evidence_status: PASS / INVALID / NOT_PROVIDED;
- manual_completed: only private_recipient_visible_send_and_edit if
  valid evidence was explicitly supplied;
- manual_pending: all other human release blockers;
- release_authorized: ALWAYS false.

During the current outage, the protected evidence was accepted PASS,
but automated live_health remains FAIL. Preflight therefore still
returns exit code 1. With all automatic health checks passing but
remaining human gates, the prerelease exit code is still 2.
There is no path to exit 0 / automatically authorize a release.

This is **not cryptographic signing** or evidence of user approval for
deployment. The stored record documents the earlier human client
observation only. A separate explicit decision is required to install
systemd users, Docker mounts, publish 0.5.14-hw, or replace SQLite
history during rollback.

## Regression

    PYTHONPATH=.:tests python3 -W default -m unittest tests/test_em50_release_preflight.py -v
    PYTHONPATH=.:tests python3 -W default -m unittest discover -s tests -q
    scripts/quality.sh
    bash tests/test_release.sh
