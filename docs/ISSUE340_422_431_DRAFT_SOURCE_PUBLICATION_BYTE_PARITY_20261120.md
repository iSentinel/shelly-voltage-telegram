# Issues 340/422/431: source release draft asset byte parity

Date: 2026-11-20, Europe/Kyiv
Scope: future GitHub Actions release workflow, QA-only tests.

## Risk

Successful source Sigstore signing is not enough to guarantee the
public GitHub Release actually includes the same TAR bytes, the
Sigstore bundle and SHA256SUMS. The existing public v0.5.25-hw
release lacks the source Sigstore bundle. That observation by
itself does NOT establish whether an old CLI upload partially
failed or a former workflow omitted the bundle.

The GitHub CLI release create command already uses an intermediate
draft when attaching assets, but its default pipeline does not
independently re-download all uploaded files for local byte parity.

## Future release workflow

After a matched public OCI signature/provenance gate succeeds:

1. Abort if a release (including an existing draft) already exists
   for the tag. Create a draft GitHub Release with --verify-tag.
2. Upload exactly three signed source assets: source TAR, its
   .sigstore.json bundle and SHA256SUMS. Never overwrite existing
   assets with --clobber.
3. Fetch the draft release assets listing via GitHub CLI and
   require exact tagName, isDraft=true, three distinct asset
   names and sizes matching the signed local release outputs.
4. Re-download all draft assets into a temporary directory;
   compare each exact file byte-for-byte with its original
   signed input. The temporary directory is always deleted.
5. Only after all verification succeeds, publish the draft with
   gh release edit TAG --draft=false and check isDraft=false.

A failed upload, wrong file list, damaged byte stream, failed
download, wrong tag or existing release exits nonzero. It does
NOT publish an incomplete draft automatically. Failed drafts
remain for explicit diagnosis rather than silent overwrite,
which is an intentional, fail-closed release recovery policy.

## QA results

- Workflow YAML and Bash parse PASS.
- Mocked GitHub CLI executable tests: 34/34 source workflow and
  draft publication policy tests PASS.
- Test cases include successful all-three roundtrip, missing
  Sigstore bundle, tampered downloaded bundle, nondraft release,
  existing tag release and upload error.
- Ruff0.14.2 formatting and project maintainability gate PASS.
- No real GitHub Release/draft/tag, OCI image, systemd unit, Docker
  container, Telegram message or production SQLite was changed.

The above are source-only mocked GH API tests. A real future signed
release still needs public source+OCI cryptographic verification,
a trusted deployment receipt and actual cross-version
WAL/PHOTO/Telegram safe rollback before live activation.
