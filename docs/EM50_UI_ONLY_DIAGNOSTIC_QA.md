# EM-50 UI-only read-only readiness diagnostic

The design-only option is a **diagnostic experiment, not a release profile**.
The installed 0.5.13-hw is not touched, and no release authorization
is produced. It is intentionally fail-closed and never returns exit code zero.

The candidate-only deploy/compose.ui-only.example.yml explicitly sets
TELEGRAM_YASNO_STATUS_ENABLED=0. The diagnostic resolves the prospective
candidate's base Compose plus this one overlay using Docker Compose's
read-only config --format json; it processes resolved environment variables
in memory and emits only PASS/FAIL. It refuses a flag that is missing,
enabled or has any value other than string zero. Because private
administrative previews may read YASNO cache independently of the public
channel feature flag, the overlay also forces YASNO_CACHE_FILE,
YASNO_TRUSTED_CACHE_FILE and YASNO_GROUP to empty strings.
The diagnostic rejects effective nonempty or missing preview controls
and refuses any YASNO reader-cache mount.
Do not print or persist docker compose config:
it may contain passwords and tokens.

This is **not** proof of an approved deployment command; every future
release must separately verify that its actual Compose invocation
matches the inspected configuration. Do not silently add other overlays.

It reuses the regular strict release preflight, including live meter
health, SQLite integrity, backup freshness, **whole-history parity**,
private Telegram client receipt and version-marker checks. It omits
the YASNO cache **only from the candidate's diagnostic eligibility**;
the full release preflight still reports its cache as FAIL.
An independent read-only posthoc audit must confirm 15 minutes of
real Shelly phase readings after the closed incident. A separate
configured-normal-voltage gate checks the latest complete rolling
15-minute same-phase window using the existing bounded stability
verifier (at least 60 samples, maximum gap 30 seconds), and compares
its minimum and maximum strictly inside the persisted operator
warning limits. Connection stability can PASS during a brownout:
such a window must stay BLOCKED. The normal-band threshold/capture
baseline still requires independent operator review. All manual
release and rollback authorization remains blocked.

Example read-only command (not a deployment):

    python3 scripts/em50-ui-only-readiness.py \
      --candidate-root /path/to/pinned-source-candidate \
      --runtime-root /opt/shelly-voltage \
      --db /var/lib/shelly-voltage/voltage.sqlite3 \
      --backup-dir /var/backups/shelly-voltage \
      --incident-id 19 \
      --acceptance-evidence /path/to/private-mode-0600-receipt \
      --json

The tool never starts or stops a container, sends Telegram messages,
writes production SQLite, fetches YASNO or installs a systemd unit.
Exit 1 means technical checks blocked; exit 2 means prerequisites
look acceptable only for human review, **never** permission to deploy.

Before any real UI-only release, require explicit approved scope,
independent review of exact pinned source, real Telegram text/chart
visual sign-off, matching quiesced backup and rollback rehearsal,
verified effective Compose for the signed release and signed approval.
A Telegram HTML widget beta is not required for text/PNG designs.
