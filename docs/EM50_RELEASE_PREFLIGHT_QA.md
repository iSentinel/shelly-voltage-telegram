# EM-50 0.5.14-hw read-only release preflight

This is a separate, **read-only** QA health and release-status report.
It does not promote an image, edit Telegram messages, install systemd
units, restart the bot, change Shelly or write to production SQLite.

The candidate VERSION is 0.5.14-hw. The installed bot may still run
0.5.13-hw; the older published RELEASE_VERSION and historical
LIVE_VERSION markers intentionally remain unchanged.

## One-command QA inspection

From the candidate source tree:

    python3 scripts/em50-release-preflight.py --help

    python3 scripts/em50-release-preflight.py \
      --cache /tmp/shelly-yasno-handoff-20261108/yasno.json \
      --json

For a future reviewed production installation, the default reader
path is /var/lib/shelly-yasno-reader/yasno.json. This path is **not**
currently installed. With the default on an unprepared live system,
the reader-cache check is expected to FAIL.

The tool makes only bounded GET requests to localhost /health and
/ready; redirects, non-loopback hosts and credentials in URLs are
forbidden. It checks production SQLite using SQLite URI mode=ro,
the saved status-message key without displaying its value, the newest
backup SHA256/age, candidate version markers, and a verified recipient
YASNO cache with correct owner, mode, group, publication and
capture timestamp.

It does NOT contact Telegram. Earlier read-only Telegram
getMe/getChat/getChatMember checks independently confirmed bot rights,
but only a user observing the real Telegram client can verify
recipient-side delivery and edits.

## Exit codes

- 0: reserved; **never returned by this prerelease gate**.
- 1: one or more automated checks failed.
- 2: automated checks passed, but required manual release
  acceptance is still **PENDING**.

The report always sets release_authorized=false. Manual pending gates:

1. Private recipient-visible Telegram send/edit observation.
2. Actual systemd activation/security review in LXC110.
3. Explicit release, backup and rollback authorization.

A manual approval from the operator cannot be inferred from a
successful Docker image build or GitHub CI.

## Regression and safety

    PYTHONPATH=.:tests python3 -m unittest tests/test_em50_release_preflight.py -v
    PYTHONPATH=.:tests python3 -m unittest discover -s tests -q
    scripts/quality.sh

The preflight does not expose Telegram token, chat ID or message ID.
The freshest SQLite backup has a SHA256 sidecar verified against
its content; a previous independent verification additionally
performed SQLite integrity checks on the three latest backups.
No backup is restored or overwritten by the preflight.
