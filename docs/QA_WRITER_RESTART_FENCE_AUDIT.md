# Read-only writer/restart-fence readiness audit

The script scripts/writer-restart-fence-audit.py only reads a **fixed**
localhost readiness endpoint and selected non-secret systemd/Docker
properties. It never stops a container, edits a service, disables a
timer, modifies SQLite or sends a Telegram request.

Run:

    python3 scripts/writer-restart-fence-audit.py
    python3 scripts/writer-restart-fence-audit.py --jsonl
    python3 scripts/writer-restart-fence-audit.py --help

The report lists independent launch/restart owners. An observed
stopped container or clean read-only preflight does NOT prove the writer
will remain fenced, and never sets writer_fenced or
owner_maintenance_authorized to true. The script does not attempt to
perform a maintenance operation or inspect secret updater receipts.

## Live offline read-only evidence (2026-11-14)

- shelly-voltage-compose.service: active, enabled, Type=oneshot.
- shelly-voltage-bot: running and Healthy; Docker policy unless-stopped.
- shelly-voltage-update-check.timer: enabled, currently inactive.
- Shelly physical meter offline, localhost /ready HTTP 503.
- Result: BLOCKED with separate reasons for active Compose, enabled
  startup, running Docker writer, restart-policy review, enabled
  update timer and unavailable meter readiness.
- Fail-closed regression cases cover on-failure Docker restarts,
  unexpected systemd service type, static unit auto-start ambiguity,
  missing timer and linked updater timer states.
- 18 dedicated tests PASS and full Ruff 0.14.2 quality PASS.
- Production source v0.5.15-hw remains mismatched with signed image
  v0.5.21-hw; PHOTO remains NO_GO.

## Next actual maintenance gates

Before any independently authorized production reconciliation:
1. Require stable natural Shelly recovery and owner approval.
2. Confirm the immutable signed image and protected updater receipt.
3. Design a bounded restart fence covering Docker, systemd, updater
   timers, watchdogs and boot. Suppress restart sources safely, and
   independently verify writer quiescence and rollback.
4. Capture fresh exact stopped-writer A/B source + whole SQLite, verify
   full restore parity and preserve the latest live outage history.
5. Reapply only matching signed source, never rebuild the signed image.
6. Verify boot continuity, telemetry readiness, identical Telegram
   message identity and unrelated pinned posts before PHOTO cutover.

This tool and document do NOT execute any of those steps and cannot
override earlier administrative denial of production source install.
