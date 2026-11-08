# Release candidate: EM-50 Telegram status + YASNO

Candidate version: 0.5.14-hw (unpublished).
Current running production: 0.5.13-hw at the last Gateway check.
Published RELEASE_VERSION marker: 0.5.12-hw.
Historical LIVE_VERSION marker: 0.5.6-hw.

The last two markers are deliberately NOT changed. In particular,
the historical LIVE_VERSION file is not a reliable statement about
the current Docker container. Actual /health and docker state must
be inspected before any release.

The candidate includes the existing Shelly voltage bot, a
default-OFF YASNO channel text addition, opt-in private widget
preview, hardened bounded public YASNO parser/cache/refresh,
the container import smoke-test, and comprehensive offline QA.

It is NOT an official Telegram HTML widget channel release.
The current stable Bot API text-edit path is the supported
fallback. Native HTML widgets remain a separate capability.

## Prerelease gates

1. GitHub QA PR dependency chain and Python tests must PASS.
2. Docker image must build and import app inside a networkless
   container with the unprivileged UID.
3. Reproducible source archive must exclude .env and secrets.
4. Verify YASNO trusted cache inside the target bot container
   using a reviewed read-only volume and UID strategy.
5. Get a real recipient-visible Telegram status/refresh test.
6. Prepare a backup, stable previous image reference and a
   tested rollback path before any production change.
7. Explicitly approve a versioned release and promotion gate.

Do not start the example systemd timer or enable the
TELEGRAM_YASNO_STATUS_ENABLED environment flag yet.

## QA commands

    scripts/quality.sh
    PYTHONPATH=.:tests python3 -m unittest discover -s tests -q
    scripts/release.sh --dry-run
    bash tests/test_release.sh

    python3 scripts/image_release_policy.py --event workflow_dispatch --ref refs/heads/qa-em50 --build-ref HEAD --image-tag manual-em50-rc1 --root .

No stable signed Git tag should be published while
RELEASE_VERSION and VERSION differ. This is intentional.

## Future rollout / rollback (not executed now)

An authorized rollout must retain production SQLite, messages,
secrets, and the current image. The new text card can be
disabled by removing TELEGRAM_YASNO_STATUS_ENABLED=1 and
restarting only as part of a reviewed configuration rollout.
Do not delete any Telegram message IDs or outage history.

The physical Pro 3EM / 3CT63 hardware has not arrived. It is
not required for an EM-50-only text-card release.
