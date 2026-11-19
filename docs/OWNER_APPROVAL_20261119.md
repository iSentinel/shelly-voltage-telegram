# Shelly monitoring bot — Owner Approval record

Date: 2026-11-19 (Europe/Kyiv)
Source: project owner's explicit in-chat declaration:
"Owner Approval даю"

Decision: **Owner Approval GRANTED in principle** for the planned
production release of the Shelly monitoring Telegram bot.

This is an owner decision, not a cryptographic attestation, a
release-signing operation, a permission to skip required QA, or a
command to install any arbitrary source/image build.

## Scope

- Project: shelly-voltage-telegram monitoring bot, Pro3EM / L1-L2-L3.
- Production: Proxmox LXC110, /opt/shelly-voltage.
- Development candidate: local branch integration-issue340-422-431,
  latest checked source commit feea6e76ffa6291631bc6efe9dd637bc75058b0c
  at the time of approval.
- Exact new signed release tag / source SHA256 / OCI digest:
  not yet established. Do not treat a future, changed build as
  implicitly approved without release-specific artifact matching.
- Approval permits progressing through the acceptance and release
  preparation stages. No production cutover has been performed.

## Technical release gates, still required

1. Complete the real signed updater/rollback integration with
   protected root pending, exclusive writer lease and sole systemd
   owner. Do not permit legacy auto-recovery to replace newer SQLite.
2. Preserve and verify full newest SQLite/WAL L1/L2/L3 history and
   Telegram PHOTO identity through the update/rollback transaction.
3. Verify exact old and new signed OCI/source/receipt pairs with
   real Cosign provenance and immutable digests; QA-only identity
   checks with signature_verified=false do not count.
4. Run hard interruption and recovery acceptance at the real update
   boundaries. A controlled Python exception and QA guest reboot
   are passed, but the updater-process SIGKILL boundary is open.
5. Run exact-head Python 3.11, 3.12 and 3.13 CI with all required
   release and rollback checks.
6. Review a concrete release manifest including the final commit,
   signed tag and OCI digest before actual promotion.

Production stays on the existing signed v0.5.25-hw build until all
technical acceptance gates have passed. This document is not to be
loaded by runtime code as a bypass token or authorization credential.
