# v0.5.16-hw release-promotion preflight (not deployed)

This isolated branch is a **proposal only**. PR #300 remains Draft.
It must not be merged or tagged without a reviewed visual acceptance
and the established release procedure.

## Scope

- Review Design Polish v4 private preview (correct В baseline,
  smooth typography, past/current/future YASNO half-hours, offline
  privacy) with the owner. Bot API delivery alone does not prove
  recipient-visible acceptance.
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
- Photo conversion of the same Telegram message ID requires an
  independent photo receipt, recipient-visible acceptance, and
  media-safe rollback. Do not restore a text-only runtime over
  a live photo without reconciling media identity.

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
python3 scripts/graphic-emergency-restore.py --arm --execute   --minutes 15 --owner-approval-file   /opt/shelly-voltage/validation/telegram-graphic-owner-approval-20261110-223420.json
systemctl is-active shelly-graphic-emergency-restore.timer
~~~

The watchdog must be **active before any stop**. It pins a root-only
copy outside the candidate checkout and starts a separate systemd
timer. After 15 minutes it restores the original service and Docker
restart policy; if restoration fails, it retries every minute.
The maintenance process must require a VERIFIED watchdog.

For the writer-stopped window, use a **runtime-only systemd mask**
rather than permanently disabling the boot-enabled service. This
allows systemd to start the service again after an LXC reboot.
Only a reviewed maintenance procedure should then execute:

~~~bash
docker update --restart=no shelly-voltage-bot
systemctl mask --runtime --now shelly-voltage-compose.service
systemctl is-active shelly-voltage-compose.service || true
systemctl is-enabled shelly-voltage-compose.service || true
~~~

The A/B and exact Telegram guards must see inactive Docker, inactive
systemd, runtime mask, and Docker restart=no. Revalidate the
watchdog between each stage. The watchdog may reactivate the writer;
in that case, stop, discard staged archives, and require fresh A/B.

After successful restoration and health review, cancel the timer
using the same copied exact script:

~~~bash
python3 /usr/local/sbin/shelly-graphic-emergency-restore   --disarm --execute
~~~

Never disarm before the source, Docker, systemd and Telegram
state have been independently reconciled. An offline physical Shelly
does not prove a restored mains supply; readiness must remain honest.

## Operator dry-run

~~~bash
# In an isolated candidate checkout only:
./scripts/release.sh --dry-run
PATH=/opt/shelly-quality-venv/bin:$PATH ./scripts/quality.sh

# On production, once the signed release exists:
bash /opt/shelly-voltage/scripts/apply-update.sh   --version 0.5.16-hw --dry-run
~~~

No stopped-writer snapshots, image tag, Telegram edits, or
production deployment are performed by this promotion proposal.
