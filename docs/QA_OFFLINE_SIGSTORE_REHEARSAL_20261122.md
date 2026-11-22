# Offline Sigstore signature rehearsal

Scope: previously published 0.5.26-hw, commit
`e70473099b168cd077d555b6abc668db3ed3dd14` only. The disconnected
LXC114 guest has loopback only. No network or production changes.

## Trust and exact artifacts

On connected Proxmox, initialize Cosign v3.0.6 with its embedded
Sigstore TUF root in a new private HOME, using the default TUF mirror:

```sh
HOME="$PRIVATE_TUF_HOME" /usr/local/bin/cosign initialize --timeout 45s
```

Transfer the authenticated trusted_root.json target, exact original
release archive, public bundle, SHA256SUMS, and their transport checksums
into a private QA directory. The copied trust root depends on the
trusted Proxmox operator and successful TUF refresh; a transfer checksum
does not authenticate an arbitrary trust root.

Verify archive structure and checksum using the existing verifier's
read-only checks. Verify the real source bundle on disconnected QA:

```sh
cosign verify-blob --trusted-root trusted_root.json \
  --bundle shelly-voltage-telegram-0.5.26-hw.tar.gz.sigstore.json \
  --certificate-identity "https://github.com/iSentinel/shelly-voltage-telegram/.github/workflows/release.yml@refs/tags/v0.5.26-hw" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  --certificate-github-workflow-ref refs/tags/v0.5.26-hw \
  --certificate-github-workflow-repository iSentinel/shelly-voltage-telegram \
  --certificate-github-workflow-sha e70473099b168cd077d555b6abc668db3ed3dd14 \
  shelly-voltage-telegram-0.5.26-hw.tar.gz
```

## OCI signature

On connected Proxmox, use the immutable published index reference:

```sh
IMG=ghcr.io/isentinel/shelly-voltage-telegram@sha256:cd8ee5e8044f924e089fc9f603b299b515746fb816b0305818db2475177f7b49
cosign save --dir "$PRIVATE_LAYOUT" "$IMG"
cosign download signature "$IMG" > oci-signature-download.json
```

Cosign save preserved the original OCI index bytes but did not save
the associated new-format signature. Local-image verification correctly
failed. The separate downloaded signature is a Sigstore DSSE bundle
with the predicate URI https://sigstore.dev/cosign/sign/v1 and one
subject matching the original OCI index SHA256.

Transfer the original raw index blob and original bundle, compare
transfer checksums, and verify on disconnected QA:

```sh
cosign verify-blob-attestation \
  --type https://sigstore.dev/cosign/sign/v1 \
  --trusted-root trusted_root.json --bundle oci-signature-download.json \
  --certificate-identity "https://github.com/iSentinel/shelly-voltage-telegram/.github/workflows/image.yml@refs/tags/v0.5.26-hw" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  --certificate-github-workflow-ref refs/tags/v0.5.26-hw \
  --certificate-github-workflow-repository iSentinel/shelly-voltage-telegram \
  --certificate-github-workflow-sha e70473099b168cd077d555b6abc668db3ed3dd14 \
  --check-claims=true oci-index.original.json
```

No insecure certificate, SCT, transparency log, or claim flags used.
The wrong Git SHA was refused for both signatures, and the modified
raw OCI index was refused. No receipt was created.

## Remaining gates

This rehearsal verifies source signature and OCI index signature, not
complete OCI descriptor/content/provenance graph parity, root trust
rotation policy, Docker RepoDigest binding, a strict runtime start, or
deployment authorization. The existing online production verifier is
unchanged. Do not substitute these logs for a staged paired receipt.
