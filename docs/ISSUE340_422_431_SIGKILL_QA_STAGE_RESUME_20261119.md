# Issues 340/422/431: updater SIGKILL rescue and same-image replay

Date: 2026-11-19, Europe/Kyiv
Status: QA-only source changes, no production deployment.

## Why this stage was needed

The protected update protocol previously proved pending, stopped writer,
and full SQLite/WAL/PHOTO rescue, but did not demonstrate a real
updater-process crash between these steps. The old production
apply-update.sh --recover is NOT safe for newest SQLite history:
it may restore an older pre-update copy and remove newer WAL content.

The disposable stage-guarded-update.py now has two QA-only options:
- --qa-crash-at {pending,writer-stopped,rescue-written,receipt-written}
  Self-terminates the current QA updater with SIGKILL at a checkpoint.
  It cannot target another PID.
- --resume-pending revalidates the exact shared pending token, Docker
  isolation, no-autonomous-restart policy and root-controlled paths.
  It stops only the selected disposable QA writer if needed, creates
  the newest full WAL rescue if absent, or proves the existing rescue
  equals the current SQLite logical history and PHOTO identity under
  the owner lock. It restores a missing QA receipt only after parity.
  The pending marker remains set; it never activates or rolls back
  any production release.

The existing --inspect and --recover-receipt remain fail-closed.
A malformed rescue, forged digest, wrong pending token, or unsafe path
must not be overwritten or silently repaired.

## Real isolated LXC114 acceptance

Executed tests/qa_paired_rescue_runtime.sh, using:
- disposable no-network Docker container, UID 10001, restart policy no;
- separate private /var/tmp/svt-qa-* SQLite and /var/lib/svt-qa-*
  rescue/fence paths; exact source mount read-only;
- synthetic three-phase Pro3EM data and PHOTO media metadata;
- no production secrets, no Telegram sends, and no GHCR promotion.

All four updater process SIGKILL points returned 137, preserving the
root-owned pending marker. The explicit resume recovered newest WAL and
PHOTO with no old source or database restore, verified full history,
generated or validated the private receipt, and kept writer stopped.

Existing QA signed-pair identity checks, tampered-L3 refusal, forged
receipt refusal, missing-Cosign refusal and target-version drift
refusal all passed with pending protected.

After the final source QA checks, attempted wrong-token pending clear
was rejected. The independent supervisor returned SKIP_PENDING and
did not start Docker. A controlled QA-only matching-token clear
allowed supervised startup of the SAME offline QA image. History
prefix remained byte-for-byte identical, PHOTO identity persisted,
SQLite quick_check was ok, L1/L2/L3 row counts remained equal and
rose from one to two per phase. Docker exited and all disposable QA
objects were cleaned. This is NOT a signed image change.

Focused QA policy tests pass with ResourceWarning treated as an
error. Project Ruff 0.14.2 and maintainability gate pass.

## Remaining production release blockers

These tests exercise the already available offline QA image with
synthetic source/OCI identities; they do NOT make an actual release
candidate. No production update, rollback, systemd cutover, Docker
restart, SQLite write, Telegram API call or release tag was performed.

A real signed new release requires exact immutable old/new OCI and
source provenance, an approved single-root systemd owner cutover,
paired release-version receipts, newest WAL/PHOTO rescue and actual
rollback/forward migration under one durable root marker. It must
also reject startup during unresolved errors; do not run the legacy
boot-time --recover. The successful GitHub 3.11/3.12/3.13 matrix from
run 44494803807 covers the prior source SHA, not this new work.

## Commands

    python3 scripts/stage-guarded-update.py --help
    bash tests/qa_paired_rescue_runtime.sh --help
    bash tests/qa_paired_rescue_runtime.sh

The final command is only allowed on the disposable QA LXC114 host
named shelly-docker-reboot-qa; it is NOT a production procedure.
