# Owner release review packet — no authorization implied

The project owner's November 19 declaration was **GRANTED IN PRINCIPLE**
for general release preparation against old source commit
`feea6e76ffa6291631bc6efe9dd637bc75058b0c`.
`docs/OWNER_APPROVAL_20261119.md` explicitly says future Git tag,
source checksum and OCI digest were not yet established. Do NOT reuse
that declaration as approval of the newer 0.5.26-hw candidate SHA.

`scripts/qa-owner-release-review.py` checks a 0600 root-owned local
source QA manifest, a 0600 local snapshot of the complete GitHub CI
run and the historical in-principle owner approval. The CI JSON is only
an **untrusted snapshot** of run metadata, not independent or signed
authentication. The tool never creates/records owner approval and
does not publish images, create tags, read a production SQLite DB or
call Telegram.

A PASS means **the review packet has sufficient syntax and matching
scope to show the owner**, not that the owner approved it. Every result
has decision=NO_GO and release_authorized=false. A complete production
release approval must explicitly name the final Git commit, source tar
SHA256, signed OCI index digest, release/tag scope and activation gate;
it must be captured through a protected owner-controlled process,
not synthesized from QA JSON. Approval to sign/publish and later
approval of production activation remain distinct decisions.

Read-only QA example:

    gh run view 44667273645 --repo iSentinel/shelly-voltage-telegram \
      --json headSha,status,conclusion,jobs > /PRIVATE/ci.json
    chmod 0600 /PRIVATE/ci.json

    python3 scripts/qa-owner-release-review.py --qa-only \
      --version 0.5.26-hw \
      --commit add29de629f4b783f3e6a9635ef8d01ec3858d7a \
      --source-manifest /var/lib/svt-qa-release-manifests/source-add29de629f4b783f3e6a9635ef8d01ec3858d7a.json \
      --ci-snapshot /PRIVATE/ci.json \
      --historical-approval docs/OWNER_APPROVAL_20261119.md \
      --jsonl

Do not treat a green CI job, checksum, or previous in-principle
agreement as a replacement for the real source/OCI Cosign signatures.
Do not trigger the version tag workflow before explicit release-specific
Owner Approval, and do not change production until technical cutover
acceptance and separate activation approval.
