# Proposed owner-gated release workflows — QA-only branch

This is SOURCE CODE PROPOSAL ONLY, not active GitHub protection.
No release tag, GHCR image or GitHub environment/ruleset was created.

The unprotected workflows currently ship on GitHub main. On the
separate QA branch, the proposed `.github/workflows/release.yml`
binds job `release` to `shelly-release-approval`. The proposed
`.github/workflows/image.yml` binds stable-tag builds to that
environment but leaves main/manual development builds on
`ci-image-build`, with no owner publication reviewer required there.
The owner review workflow must be usable for a sole-owner repo;
a self-review prohibition could deadlock. This must be verified
through a real owner-controlled GitHub Environment configuration.

Both tag publication jobs run `qa-release-tag-protection.py` BEFORE
any source signing, OCI registry login, build/push or release upload.
They fetch all repository/inherited rulesets with **full details**
and environments via GitHub API. A missing/unauthorized API response,
no v* tag restriction, no actual required environment reviewer,
or missing job binding causes fail-closed nonzero exit. The GitHub
Actions token may lack the administrative GET permission; that
error MUST block publication rather than be silently ignored.

A job environment with no configured required reviewers may be
auto-created without protection. Therefore the owner must explicitly
configure and verify the GitHub ruleset/environment first; merely
merging these YAML changes or assigning an environment string does
not create protected release authorization. Merge/rebase of this
proposal also changes the exact source SHA and reproducible TAR
checksum, invalidating the old frozen candidate's release identity.

Appropriate order:

1. Review this QA-only workflow patch and run its entire CI matrix.
2. Under a separately authorized GitHub settings change, configure
   a mandatory owner-reviewed environment and a v* tag-creation
   ruleset with deliberate bypass policy.
3. Re-audit via fresh authenticated GitHub evidence, verify tag job
   execution can complete after actual owner review.
4. Establish a NEW exact source SHA/version archive digest, get
   owner approval for specific signing/publication and perform a
   protected signed image/source release.
5. Independently verify Sigstore source, OCI index, SBOM and both
   platform attestations, then complete isolated writer/cutover QA.
6. Request separate explicit Owner Approval before LXC110 activation.

No step authorizes unsafe old v0.5.25-hw image rollback, changing
live SQLite/WAL, or sending public Telegram messages.
