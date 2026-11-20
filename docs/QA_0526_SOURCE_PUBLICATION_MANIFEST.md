# QA-only exact-source publication manifest for 0.5.26-hw

The release workflow uses the selected Git commit's **committer
timestamp** (`git show -s --format=%ct SHA`), not a fixed epoch of
zero. An archive built with SOURCE_DATE_EPOCH=0 is reproducible but
will NOT have the same hash as a tagged GitHub Source Release.

`scripts/qa-release-publication-manifest.py` is deliberately
non-deploying. It requires a clean local Git tree at one immutable
SHA, validates the version markers, SHA256SUMS, source tar members
and rebuilds the source archive from that **exact SHA** and exact
commit timestamp in a private temporary directory. Both complete
archive bytes must be identical. It never writes production data,
starts Docker, invokes GitHub publication, contacts Shelly or Telegram.

Optional output is an exclusive root-owned 0600 QA record
`/var/lib/svt-qa-release-manifests/source-<SHA>.json` in a preexisting
root-owned 0700 directory. A second write cannot replace the record.
The JSON is an unsigned QA evidence record, **not** a signed or
immutable production release receipt. An old hash from epoch zero
must be considered invalid for the tag publication.

The record always reports CI authentication, Sigstore source
identity, OCI signature, attestations, Owner Approval and release
authorization as false. This remains true if source-byte parity
passes. Subsequent independent verifiers MUST check the real
published signed blob and OCI pair; do not copy flags from a file.

An example QA-only command on a clean, versioned candidate checkout:

    python3 scripts/qa-release-publication-manifest.py --qa-only \
      --repo /root/svt-forward-repair-0.5.26-20261120 \
      --version 0.5.26-hw --commit FULL_40_HEX \
      --archive /root/svt-qa-0526-tag-epoch-source-20261120/shelly-voltage-telegram-0.5.26-hw.tar.gz \
      --sha256sums /root/svt-qa-0526-tag-epoch-source-20261120/SHA256SUMS \
      --jsonl

No tag or GHCR build may be triggered by this QA tool.
