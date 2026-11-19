# Issues 340/422/431: join pending rescue and signed-pair receipt

Date: 2026-11-19, Europe/Kyiv
Scope: source-only QA on isolated LXC114. Production unchanged.

## Previously separate steps

A root-pending writer fence protects SQLite transactions while the
newest full SQLite/WAL/PHOTO rescue and QA receipt are created.
Separately, the signed source/OCI stager copies the exact TAR, bundle
and SHA256SUMS, verifies those copied bytes and writes a private
deployed.json receipt last.

Those steps must not produce a deployable-looking receipt for a
different pending update. The candidate sole-owner supervisor must
not accept an old receipt that lacks a transaction token.

## Joined QA gate

The QA-only signed-pair stager now:
- requires explicit container, database, root fence directory, latest
  rescue output and a qa-prefixed transaction token in --qa-stage mode;
- checks the real disposable container restart:no, network:none,
  read-only rootfs, UID10001 and exact fence/SQLite/source mounts;
- checks the root-owned token-bearing pending marker and verifies
  the stopped writer, complete newest live-vs-rescue SQLite logical
  history including WAL and PHOTO identity, and exact SHA256 receipt;
- repeats this check after the cryptographic pair verifier returns
  PASS and before writing deployed.json;
- stores the exact token inside the final paired receipt, along with
  signed source/OCI SHA, immutable OCI digest and private source hash;
- never clears pending or starts, switches, removes or rolls back
  any Docker image.

The candidate sole-owner supervisor additionally refuses a paired
receipt without a well-formed transaction_token. This validates
syntax only; complete production journal-to-token replay protection
and cryptographic provenance still require a trusted release writer.

## Actual QA evidence

- Unit tests: 34/34 PASS for joined receipt/supervisor candidate
  policies; Ruff0.14.2 and maintainability/quality checks PASS.
- Real LXC114 synthetic Docker writer: actual 4 updater SIGKILL
  boundaries, newest WAL/PHOTO parity and full L1/L2/L3 parity PASS.
- Real pending marker with wrong token refused before creating a
  source stage. Correct token was rechecked after fake cryptographic
  proof, final root-private receipt included the same token, its
  source tar SHA matched, and the candidate receipt policy accepted
  its structure. Pending remained set.
- Real LXC114 preflight still refused unavailable Cosign, changed L3,
  forged rescue hash, target version drift and wrong token. Matching
  manual marker clear + SAME offline QA image supervisor start
  continued history without deleting preceding rows; counts L1/L2/L3
  went from 1 to 2.
- No GHCR or signing action was made in this joined QA test: the
  positive source+OCI signature evidence was MOCKED and the
  source TAR was synthetic unsigned data. Never interpret as
  an actual signed new release.
- The broad containerized regression on this exact new HEAD was
  not completed; follow up with exact-head remote GitHub CI on
  Python3.11/3.12/3.13. Do not infer it from earlier CI results.
- QA Docker, pending marker, joined source receipt and rescue
  fixture were all cleaned; no production data or service changed.

## Remaining release blockers

Need an actual new signed source TAR + OCI index pair on one immutable
Git SHA; an authenticated production writer for deployment receipt;
a durable transaction journal that binds pending token, signed pair,
newest full WAL/PHOTO+Telegram intents and the only systemd writer
owner; and actual image-change/reverse-rollback acceptance with
process interruption and guest reboot. The live v0.5.25-hw service
still has its legacy --recover hook, so no production cutover yet.

## QA commands

    python3 scripts/qa-stage-verified-pair-receipt.py --help
    python3 -m unittest -q test_qa_signed_pair_stager
    bash tests/qa_paired_rescue_runtime.sh

The live QA script requires root on the disconnected LXC114 host
shelly-docker-reboot-qa and never contacts a real meter or Telegram.
