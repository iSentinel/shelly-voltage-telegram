# Signed source + OCI independent post-publication QA

Before a new 0.5.26-hw tag/release can be used for a production update,
an owner must separately approve the exact Git SHA and protected tag
publication plan. **Pushing a v* tag triggers automated OCI publication
and source release workflows.** No tag must be created merely to
satisfy test or preflight tooling.

The read-only scripts/qa-postsign-release-acceptance.py joins:

- Public source release asset inventory and the peeled v* tag commit,
  via the existing qa-release-asset-inventory.py checker.
- Real source Sigstore blob verification and signed multiarchitecture
  OCI index, SBOM and provenance verification, via
  verify-signed-release-pair.py. Both certificates must bind the SAME
  exact Git SHA, version/tag and pinned OCI digest.
- A SHA256 of the independently verified source archive.

The check NEVER publishes or changes a release, downloads/pulls Docker
images, starts production, changes SQLite/Telegram or certifies the
deployment. Offline public metadata JSON may be stale/spoofed, so the
report explicitly marks its independent authentication as false.
Re-check current release metadata and signed assets from trusted
sources before approval. The local exact-Git reproducibility manifest
must also be independently checked.

Example AFTER an authorized, already-published signed release exists:

    python3 scripts/qa-postsign-release-acceptance.py --qa-only \
      --version 0.5.26-hw --commit FULL_40_HEX \
      --digest sha256:EXACT_64_HEX \
      --release-json /private/qa/release.json \
      --tag-ref-json /private/qa/peeled-tag-ref.json \
      --archive /private/qa/shelly-voltage-telegram-0.5.26-hw.tar.gz \
      --bundle /private/qa/shelly-voltage-telegram-0.5.26-hw.tar.gz.sigstore.json \
      --sha256sums /private/qa/SHA256SUMS --jsonl

Every result, including PASS, has release_authorized=false,
rollback_authorized=false and production_modified=false. If signatures
or assets do not exist, BLOCKED exit 3 is the required safe result.

Next distinct gate: owner-approved root-controlled sole-writer Docker
cutover preserving newest SQLite/WAL, L1/L2/L3 and Telegram PHOTO,
pending send intents and cleanup queue, with actual hard-process and
reboot acceptance. Never use the older unsigned OCI as a fallback.
