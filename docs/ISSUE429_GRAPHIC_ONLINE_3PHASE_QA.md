# Issue #429: three-phase online PNG acceptance

## Defect

The source-only graphic-online-acceptance.py previously supplied only
the top-level L1 health snapshot to widget_public_data(), even
when the runtime health endpoint reported all three Pro3EM phases.
A 960x800 PNG and exit-0 PASS could incorrectly show only L1.
This was a **QA acceptance defect**, not proof that the live Telegram
recipient saw an incorrect card.

## New fail-closed contract

- One-phase historical EM-50 snapshots remain accepted.
- An explicit topology (line_labels: L1/L2/L3 and phases with
  exactly L1/L2/L3) must include all three independent snapshots.
- The L1 phase snapshot must agree with top-level L1 voltage and time.
- Current L2/L3 must each be online, less than 60 seconds old, and
  have valid numeric voltage/timestamp. Partial or stale phases cause
  FAIL, no PNG and no public messages.
- A three-phase acceptance requires TELEGRAM_GRAPHIC_STYLE=v5 and
  validates render_card_png() explicitly reported three online
  phase tiles. Old v4 one-phase acceptance remains unchanged.
- CLI success includes verified_phases to distinguish actual
  L1/L2/L3 evidence from a single-phase preview.
- Do not interpret this source-only offline acceptance as approval
  to send or edit any Telegram message.

## Verified LXC110 acceptance, 2026-11-18

A sanitized snapshot was created from real /health, containing
only connectivity, voltage, frequency, timestamp and phase labels.
The snapshot was mounted into a disposable container with
network disabled, a read-only source checkout, no secrets, disabled
container privileges, and exactly the already signed image
digest for v0.5.25-hw.

It returned PASS with verified_phases L1/L2/L3,
image size 960x800, telegram_sent=false; PNG SHA256:
4879f4c72c047ca7825859ecea50e89d8ca80f34f7f3b30714aa328236b412c4.
The 3-phase PNG stays private under
/var/tmp/svt-issue429-live-3phase-acceptance-20261118.
Actual signed production remained healthy and /ready HTTP 200.
No Telegram send/edit or database write occurred.

## QA

The host's Python environment does not have Pillow; v5 renderer tests
must therefore run in the pinned **network-isolated** production image.
This is an environment dependency, not a rendering regression.

Command to run when PINNED_SIGNED_IMAGE is set to the exact known OCI
digest and shell working directory is the QA checkout:

    docker run --rm --network none --read-only --user 10001:10001 \
      --cap-drop ALL --security-opt no-new-privileges \
      --tmpfs /tmp:rw,nosuid,nodev,size=64m,mode=1777 \
      -v "$PWD:/code:ro" -w /code \
      -e PYTHONPATH=/code:/code/tests \
      --entrypoint python "$PINNED_SIGNED_IMAGE" \
      -W error::ResourceWarning -m unittest discover -s tests \
      -p test_graphic_online_acceptance_cli.py -q

    scripts/quality.sh

This does not verify actual Telegram recipient visibility or grant
release authorization. Issues #340 and #422 remain independent
boot-continuity and signed rollback acceptance blockers.
