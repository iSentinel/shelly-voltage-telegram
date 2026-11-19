# Issues 340/422/431: verify public OCI signature after tagged signing

Date: 2026-11-19, Europe/Kyiv.
Scope: source-only QA. No actual tag or production release.

## Real-world evidence driving the change

The existing production OCI index digest is:
sha256:471981b896bd208969ad0ccc8977fe0b0db7712e0e3b1640b788d7c11d1b1c62

The historical GitHub Actions tag-build v0.5.25-hw, exact Git commit
72fb94f8501fb5b0146881a74e059bb84888ca37, produced that SAME
OCI digest. The tag workflow "Sign versioned image by digest" step
completed successfully in build run 44218556630 on 2026-11-18.

Despite that successful job, read-only external diagnostic verification
using the locally present /usr/bin/cosign (GitVersion: devel) with the
exact GitHub tag workflow identity and OIDC issuer returned exit=10
"no signatures found". Neither the legacy sha256-<digest>.sig tag nor
the OCI referrers lookup was visible to an anonymous public GHCR
client (both HTTP 404). This is NOT proof that no signature exists:
the local Cosign binary is not the pinned, trusted Cosign v3
required by the release verifier; GHCR might use or restrict
another signature-storage format. It DOES mean that public
cryptographic verification is currently NOT established.

The public source release v0.5.25-hw is also missing the
source TAR.sigstore.json bundle, so the complete signed source/OCI
pair cannot be accepted, regardless of GitHub's signing job status.

## Future release workflow fix

In .github/workflows/image.yml a separate required, tag-push-only
"Verify published release signature anonymously" step now follows
"Sign versioned image by digest". It:
- uses the exact OCI digest output by the already CI-gated image build;
- requires an exact source Git SHA and refs/tags/v* event ref;
- checks the pinned Cosign major version v3;
- builds the exact GitHub image.yml identity from its signed tag ref;
- calls Cosign verify for the SAME immutable image digest with
  OIDC issuer, workflow SHA, ref and repository constraints;
- overrides DOCKER_CONFIG with a newly created empty directory
  so the verification does not inherit docker/login-action
  credentials; always deletes that directory;
- fails closed on any Cosign lookup, identity or transparency
  verification error; no ignore-tlog/continue-on-error/fallback;
- does NOT change how manual builds or normal main builds run.

An actual future release job may now FAIL after successful signing
if its signature is not publicly discoverable. That failure is
intentional and must be investigated before treating the release
as a signed pair. It does not retroactively sign or alter the
currently deployed v0.5.25-hw digest.

## QA acceptance evidence

- Pinned release workflow and source policy tests: 34/34 PASS.
- Local YAML parse and extracted verification shell Bash -n PASS.
- Ruff0.14.2, project formatting and maintainability gate PASS.
- No image pushed, no tag/release created, no source TAR altered,
  no real signature generated, no Docker/SQLite/Telegram/systemd
  production changes.

## Unclosed production gates

A release must still have a real source TAR Sigstore bundle, a
publicly verifiable signed OCI index, matching exact Git SHA and
complete per-platform attestation evidence; then the actual
signed update and signed rollback must pass under sole writer
ownership preserving newest WAL, PHOTO and pending Telegram state.

Do not treat old successful cosign sign logs, unsigned OCI
attestation metadata or this workflow policy QA as release GO.
