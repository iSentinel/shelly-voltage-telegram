# Issue #402 — signed OCI trust baseline and candidate isolation

**Source-only QA evidence, 17 November 2026.** This note does not
approve, sign, push or deploy an OCI image.

## Official 0.5.21-hw baseline

The existing official release has tag `v0.5.21-hw` pointing to commit
`39bd933a61cbe2ace06911014e87e49df8475c39` (verified with
`git ls-remote`). The pinned OCI index is:

```text
ghcr.io/isentinel/shelly-voltage-telegram@sha256:f880ffc9748ad97a34a5d1bcf5c0ec25fb33793240de70501fd9c46651a00639
```

Signature verification on LXC110 with Cosign v3.0.6 passed
for exact GitHub Actions workflow certificate identity
`https://github.com/iSentinel/shelly-voltage-telegram/.github/workflows/image.yml@refs/tags/v0.5.21-hw`
and OIDC issuer `https://token.actions.githubusercontent.com`.
The independent OCI verifier passed for **both linux/amd64 and
linux/arm64**: checked the source revision label, the attached
SPDX 2.3 SBOM with packages, SLSA build provenance and attestation
subject/image manifest SHA256 equality.

This proves the old official index can still be verified from public
GHCR. It does **not** sign the emergency running local production image,
whose image ID and source hash differ from the official pinned OCI.

## Fail-closed negative acceptance

The same old digest was deliberately checked against the *wrong*
version `v0.5.22-hw`: Cosign failed with a nonzero exit code because
the exact workflow certificate identity did not match. The OCI metadata
auditor was deliberately invoked with candidate source SHA
`4bbc9462134c312090ef4a2e73edf68e5d44d5e5`: it failed with
`Unexpected source revision on linux/amd64`.

These are expected safety refusals, not runtime incidents.

## Repeat verified, read-only baseline

```bash
cosign verify \
  --certificate-identity 'https://github.com/iSentinel/shelly-voltage-telegram/.github/workflows/image.yml@refs/tags/v0.5.21-hw' \
  --certificate-oidc-issuer 'https://token.actions.githubusercontent.com' \
  'ghcr.io/isentinel/shelly-voltage-telegram@sha256:f880ffc9748ad97a34a5d1bcf5c0ec25fb33793240de70501fd9c46651a00639'

python3 scripts/verify-published-image.py \
  --digest sha256:f880ffc9748ad97a34a5d1bcf5c0ec25fb33793240de70501fd9c46651a00639 \
  --commit 39bd933a61cbe2ace06911014e87e49df8475c39
```

The second command verifies the OCI source/attestation metadata,
not its cryptographic signature. Both commands are necessary for an
official release. A failing command means NO-GO; do not patch
`deployed.json`, retag an emergency image or fake source labels to
make the check green.

## Next provisional candidate

The provisional `0.5.22-hw` branch has
`VERSION=0.5.22-hw` and `RELEASE_VERSION=0.5.21-hw`.
The stable tag gate must continue rejecting a tag push. A new
`v0.5.22-hw` Git tag or signed OCI does not exist at this stage.

Before any production cutover, the sole owner must review and approve
the **exact final source commit, proposed version, tag, image digest,
rollback scope and activation transaction**. CI should build and sign
a fresh immutable digest, and independent registry verification must
confirm both architectures, exact revision, SPDX and SLSA.

The sole owner previously accepted a one-physical-NVMe failure-domain
risk and the sole-owner source-review model. An independent reviewer
or second physical drive is **not required**. Local WAL-consistent
predeploy backup/restore, a single-writer production transition,
recipient-visible Telegram acceptance and separate PHOTO authorization
remain independent gates. `offhost_backup_verified=false`
is not to be misrepresented as an off-device backup.
