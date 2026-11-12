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
