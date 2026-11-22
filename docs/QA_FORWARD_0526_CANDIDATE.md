# 0.5.26-hw forward-repair candidate — QA branch only

This branch is a versioned release candidate, NOT an approved or
published release. Both VERSION and RELEASE_VERSION are 0.5.26-hw,
but LIVE_VERSION is deliberately unchanged; the running Docker bot is
still 0.5.25-hw. Source tests and reproducible release dry runs must
pass before any signed build is considered.

The branch has NO release tag, GitHub Release, signed source Sigstore
bundle, attested OCI index or production deployment. Do not publish
a version tag, dispatch release workflows, push a GHCR image, or
modify the running bot without explicit Owner Approval and all gates:

1. Exact immutable Git SHA, full green Python3.11/3.12/3.13 CI.
2. Signed source TAR/Sigstore with digest-validated reproducible data.
3. Signed, attested multiarch OCI manifest on the SAME exact Git SHA.
4. Independent public verification of the resulting source/OCI pair.
5. Real root-owned sole-writer activation with newest SQLite WAL,
   L1/L2/L3, photo identity, send intents, Telegram cleanup and
   no duplicate channel publications.
6. Safe forward repair and successful isolated hard-process/reboot
   rehearsal, with no untrusted old source or DB rewind.
7. Explicit Owner Approval of exact SHA, digests and cutover plan.

Current v0.5.25 old source TAR lacks Sigstore bundle, and the
production OCI digest lacks publicly discoverable Cosign signature.
That trust gap is NOT fixed by renaming or version-bumping source.

## Artifact repeatability requirement

Release archive modes must be deterministic across Git checkout and
clean Git archive extraction, not merely repeatable in one workspace.
All staged directories and executables use mode 0755; all
nonexecutable public files use mode 0644. Do not package from an
outdated QA copy; compare against a complete immutable Git SHA tree.
