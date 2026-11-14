# Disposable immutable OCI Storage crash acceptance

**QA-only; Issue #340. Not an install, reboot or monitoring release.**

The actual release image contains its own Python app.Storage.
This test executes only that Storage API on invented SQLite data,
not the default Telegram monitoring command. The exact immutable
OCI image must already be present locally. No network or image
download is allowed.

## Safe modes

Default, inert and runnable without Docker:

    python3 scripts/disposable-signed-oci-storage-qa.py --dry-run

Full QA with explicit operator opt-in and immutable image digest:

    python3 scripts/disposable-signed-oci-storage-qa.py \
      --run-disposable \
      --confirm DISPOSABLE_SIGNED_OCI_STORAGE_QA_ONLY \
      --image ghcr.io/isentinel/shelly-voltage-telegram@sha256:HEX_DIGEST \
      --jsonl

Replace HEX_DIGEST with exactly 64 lowercase hexadecimal characters
from the previously verified release image. The script rejects tags,
wrong registries, missing pins and arbitrary container names.
Use -h or --help for complete usage.

A successful result reports PASS, total synthetic rows 65002,
quick_check=ok and both committed/recovered sample values.
Exit code 0 is QA success only; 2 is missing consent or bad image;
3 is failure to run, verify or clean the disposable acceptance.

## No-production-change guarantees

- Default run is inert, with no Docker or SQLite access.
- Actual run must target an explicitly supplied immutable GHCR
  digest, already present locally, with Docker --pull never.
- Fixed random QA container names only; no selectable production
  container, image build, Docker Compose, systemd or Telegram call.
- Docker network=none, read-only container root, no ports, no
  secrets, nonroot UID 10001, no-new-privileges, all capabilities
  dropped, pids/memory/CPU limits and restart policy=no.
- The sole writable bind is a private fresh temporary directory
  containing exclusively invented SQLite samples and a QA marker.
- Seed and verify containers use Docker --rm. The crash container
  is forcibly killed only by its unique validated QA name and
  then removed. Cleanup failures cannot be treated as success.
- Exact ID, image, health status and restart count of the fixed
  running production container are compared before and after.
  No production source, database, Compose state or Telegram
  status message is accessed by the image's Storage test.

## Test lifecycle

1. Launch the selected immutable image with python -c instead of
   its normal monitor command; create a synthetic SQLite v3 via
   actual app.Storage.initialize and insert 65000 samples.
2. Launch another disposable instance of the same immutable image,
   append 225.4 V through actual Storage.add (committed) and add
   201.1 V in a second uncommitted transaction.
3. Wait for a private scratch marker confirming the committed write;
   verify the container uses no network and restart policy=no.
   SIGKILL only that test container (expected exit 137).
4. Start a third disposable instance of the exact image and check
   that the committed row survived but the uncommitted row did not.
   Run actual Storage.initialize and Storage.add again to append
   223.7 V. Verify 65002 rows, last two values and quick_check.
5. Verify production runtime identity unchanged, remove all
   generated QA objects, and delete the temporary synthetic data.

## 2026-11-14 Gateway acceptance

- 25/25 dedicated mocked unit tests PASS, including inert default,
  required exact consent, immutable digest validation, network and
  resource constraints, fixed production target inspection, scoped
  SIGKILL and cleanup failure reporting.
- Full project Ruff/quality PASS; functions <=40 lines.
- Actual LXC110 disposable Docker acceptance against the already
  present local signed release v0.5.21-hw immutable digest:
  65000 seeded, committed row 225.4 V retained after SIGKILL,
  uncommitted 201.1 V absent, final 223.7 V appended successfully,
  65002 rows and quick_check=ok. Exit 137, no restart.
- After hardened cleanup, the full actual acceptance was rerun
  successfully. No generated QA Docker container or scratch path
  remained, production Docker Healthy with zero restarts.
- The release's OCI signature was previously verified separately
  with Cosign v3.0.6 for its GitHub workflow certificate identity
  and issuer. This QA script does not itself perform Cosign
  verification; immutable digest identity does not imply trust.

## Remaining NO-GO requirements

This is a reproducible Storage-level crash acceptance inside the
real image. It does not execute a real Telegram monitor with network
access, validate all runtime configuration and schema migrations,
replay Docker/Compose/updater co-start on a disposable host, or
represent physical Proxmox power loss and storage-cache failure.

Installed host sources remain v0.5.15-hw while the signed running
image reports v0.5.21-hw. Real restart-owner fencing is BLOCKED.
Production source installation, current-history SQLite restore and
Telegram public TEXT-to-PHOTO/pin migration require separate
independent review, fresh fully writer-fenced source+whole SQLite
A/B and explicit owner authorization.
