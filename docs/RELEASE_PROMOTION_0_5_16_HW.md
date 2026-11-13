# v0.5.16-hw release-promotion preflight (not deployed)

This is a **preflight checklist, not deployment authorization**. The owner
accepted Design Polish v5.1; PRs #300, #301, #302, #303 and #304 have been
merged. The physical production bot remains at `0.5.15-hw`. Neither the
`v0.5.16-hw` Git tag nor signed public release artifacts are available.

The last independently tested candidate before this documentation change
was `32ae7eeedc912d3a22f2ea46ffadfbec3eabbd9e`. Recompute the exact
`main` SHA after **every** merge; do not tag the older SHA automatically.

## Current release gates (13 November 2026)

- Combined source-only regression: **1827/1827 PASS** after PR #303/#304;
  focused 16/16 PASS and quality gate 214 Python files PASS.
- Exact combined Git-tree was compared against the merge commit: PASS.
- Source-only archive was built twice with equal bytes and SHA256:
  `ef9ab67d3aa9ee427239187c1e7a70ee5ea8e9358c5966b27543edd6cb92880c`
  (846004 bytes). This is a QA artifact, **not** a published signed release.
- Real Shelly, SQLite integrity, verified YASNO 10.1 and the latest backup
  checksum passed preflight. Exact backup/live SQLite parity **fails** while
  the writer continues to append measurements. A hot backup is not a
  stopped-writer A/B cutover snapshot.
- Pending: authenticated release tag, GitHub source Release, signed Cosign
  GHCR image, private recipient-visible Telegram send/edit acceptance,
  systemd activation review and explicit release-plus-rollback approval.
- The exact public Telegram status is currently a **text** post. The channel
  has another pinned message; the status pin audit returns
  `PINNED_MESSAGE_MISMATCH`. Never change or remove that unrelated pin to
  force the cutover.

## Scope

- Preserve the accepted Design Polish v5.1 (correct В baseline, smooth
  typography, live voltage, past/current/future YASNO half-hours and
  offline privacy). Online/offline source-only PNG parity passed.
  Recipient-visible delivery is a separate, still-pending acceptance.
- Compare candidate VERSION, proposed RELEASE_VERSION, and public
  README metadata before review. The physical LIVE_VERSION remains
  unchanged and is not used to claim a production deployment.
- Run full CI, source-release tests, container-import tests and
  pinned signature-policy tests for all supported architectures.
- Promote only through a reviewed merge followed by an exact
  v0.5.16-hw Git tag. Both tag workflows must pass: signed
  multi-platform GHCR digest and filtered GitHub Release archive.
- On the production host, validate apply-update.sh with --version
  0.5.16-hw and --dry-run. It must verify the published manifest,
  archive, digest and Cosign signature **before** any service stop.
  A 404, 503, unsigned image, or stale tag remains BLOCKED.
- Require an independently tested restoration watchdog and
  a controlled stopped-writer window before complete source+SQLite
  snapshots A/B. Phase A precedes the owner-approved exact
  editMessageText receipt; phase B follows it.
- The official Telegram Bot API `editMessageMedia` supports replacing a
  text message with media. This capability **does not** establish that the
  particular public status post was successfully edited. Require the exact
  message ID, a positive API receipt, independent recipient-visible
  acceptance and a media-safe rollback. See
  https://core.telegram.org/bots/api#editmessagemedia.
- Do not restore a text-only runtime over a live photo or alter the
  unrelated pinned message. A denied or ambiguous edit is a stop condition.

## Independent restoration before A/B

The source-only helper scripts/graphic-emergency-restore.py is never
armed by tests or CI. It must first be reviewed and installed from
the exact source revision. To inspect the current service state:

~~~bash
python3 scripts/graphic-emergency-restore.py
~~~

Only after signed-install preflight PASS, visual confirmation, a
private recovery rehearsal and an approved maintenance window:

~~~bash
python3 scripts/graphic-emergency-restore.py \
  --arm --execute --minutes 15 \
  --owner-approval-file \
  /opt/shelly-voltage/validation/telegram-graphic-owner-approval-20261110-223420.json
systemctl is-active shelly-graphic-emergency-restore.timer
~~~

The watchdog must be **active before any stop**, and its separate
root-only boot-rescue service must be enabled. Before the service
is disabled, the watchdog installs its own root-owned startup
unit and a protected armed marker. Its timer restores after the
approved deadline; if LXC reboots while the writer is stopped,
the boot-rescue service restarts the monitor and retries failures.

Do not use systemctl mask --runtime for the existing installed
compose unit. On this server with systemd 257, live disposable
tests showed that it **did not prevent a manual restart**, even
after daemon-reload. Keep this proven limitation in the runbook.

After the independent timer and boot-rescue service have both
been verified, a reviewed maintenance procedure may fence the
writer using Docker restart=no and systemd disable --now:

~~~bash
docker update --restart=no shelly-voltage-bot
systemctl disable --now shelly-voltage-compose.service
systemctl is-active shelly-voltage-compose.service || true
systemctl is-enabled shelly-voltage-compose.service || true
systemctl is-active shelly-graphic-emergency-restore.timer
systemctl is-enabled shelly-graphic-emergency-boot.service
~~~

A/B and Telegram guards must reject any active writer, enabled
production startup or Docker restart policy other than no.
Recheck both independent rescue mechanisms at each stage.
If the timer restores the writer, stop immediately: do not publish
stale A/B archives or attempt to repeat a Telegram edit.

After successful restoration and health review, cancel the timer
using the same copied exact script:

~~~bash
python3 /usr/local/sbin/shelly-graphic-emergency-restore \
  --disarm --execute
~~~

Never disarm before the source, Docker, systemd and Telegram
state have been independently reconciled. An offline physical Shelly
does not prove a restored mains supply; readiness must remain honest.

## Resolve the exact source before an authorized tag

~~~bash
git fetch origin main
SOURCE_SHA=$(git rev-parse origin/main)
git status --short
git ls-remote --tags origin refs/tags/v0.5.16-hw
~~~

The source SHA in this document is evidence of a previous QA pass, **not**
an immutable tag target after new merges. Finalize review, rerun tests and
reproducible archive generation on `SOURCE_SHA`, and obtain release approval.
Only an authenticated, authorized release operator should publish the
exact tag and verify both release workflows. Do not substitute a Git
branch for the tag, an unsigned QA archive for Cosign, or a successful
dry-run for a production installation.

## Operator dry-run

~~~bash
# In an isolated candidate checkout only:
./scripts/release.sh --dry-run
PATH=/opt/shelly-quality-venv/bin:$PATH ./scripts/quality.sh

# On production, once the signed release exists:
bash /opt/shelly-voltage/scripts/apply-update.sh \
  --version 0.5.16-hw --dry-run
~~~

No stopped-writer snapshots, image tag, Telegram edits, or
production deployment are performed by this promotion proposal.
