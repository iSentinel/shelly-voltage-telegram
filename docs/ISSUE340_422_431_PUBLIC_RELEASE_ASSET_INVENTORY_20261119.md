# Issue 340/422/431: public release and exact tag asset inventory

Date: 2026-11-19, Europe/Kyiv.
Scope: source-only and public GitHub read-only QA. No release/tag created.

## Why

Previously, the signed source+OCI pair preflight depended on full,
already-downloaded signed artifacts, while the latest public source
release v0.5.25-hw had an archive and SHA256SUMS but NO
.sigstore.json. That omission must block BEFORE a future cutover
rather than degrading to an unsigned SHA256-only source.

scripts/qa-release-asset-inventory.py is a separate preliminary
public asset inventory gate. It accepts two saved HTTPS GitHub
API responses (releases/tags/vVERSION and git/ref/tags/vVERSION),
an exact 40-digit lowercase commit SHA and one sha256 OCI digest.
It never calls the remote API, Docker, Telegram, Cosign, systemd
or SQLite and cannot install, update, sign or roll back anything.

The gate fails closed on:
- wrong version, mutable commit or non-digest OCI ID;
- draft/prerelease release, incorrect tag or noncanonical release URL;
- missing, duplicate, empty, not-uploaded or wrong-URL assets;
- missing exact source TAR, SHA256SUMS or TAR.sigstore.json;
- wrong tag ref, tag object not a peeled commit, or tag SHA mismatch;
- symlinked, malformed or oversized JSON inputs.

Successful output is PASS_PUBLIC_INVENTORY_ONLY and explicitly
source_signature_verified=false,
oci_signature_verified=false,
tag_metadata_cryptographically_verified=false,
release_authorized=false,
rollback_authorized=false and production_modified=false.
This is not a cryptographic signature check and not deployment GO.
After inventory, scripts/verify-signed-release-pair.py must still
verify actual source bytes, keyless Sigstore bundle, pinned OCI
digest, source revision and both platform attestations.

## Real GitHub evidence

Fetched the public release JSON and exact Git tag reference via
HTTPS into temporary source-only QA files. The tag resolved to:
v0.5.25-hw -> 72fb94f8501fb5b0146881a74e059bb84888ca37.

Published source assets:
- SHA256SUMS (uploaded, 107 bytes)
- shelly-voltage-telegram-0.5.25-hw.tar.gz (uploaded, 952377 bytes)

No corresponding TAR.sigstore.json asset was published.
With that ACTUAL tag SHA and the currently deployed OCI digest,
the isolated LXC114 gate returned exit 3, BLOCKED with:
INVENTORY_REQUIRED_ASSETS_MISSING:
shelly-voltage-telegram-0.5.25-hw.tar.gz.sigstore.json.

QA test suite 11/11 PASS. Ruff0.14.2 and the project
quality/maintainability gate PASS. Downloaded JSON files were
removed from Proxmox and LXC114. No production runtime changes.

## Next actual release blockers

Publish a new properly signed source TAR Sigstore bundle and
signed OCI index from the same immutable CI-verified tag; verify
both actual byte-level signatures and OCI provenance; then
rehearse a guarded real cross-version rollback preserving latest
WAL, PHOTO and Telegram intents under sole-owner systemd.

Do not republish or overwrite existing release assets solely to
make this inventory pass. Do not treat a success here as approval
for any production update.

## Read-only usage

    python3 scripts/qa-release-asset-inventory.py --help
    python3 scripts/qa-release-asset-inventory.py \
      --release-json /absolute/path/release.json \
      --tag-ref-json /absolute/path/tag-ref.json \
      --version 0.5.25-hw \
      --commit 72fb94f8501fb5b0146881a74e059bb84888ca37 \
      --digest sha256:471981b896bd208969ad0ccc8977fe0b0db7712e0e3b1640b788d7c11d1b1c62 \
      --jsonl

The JSON files are externally retrieved metadata, not a trust root.
