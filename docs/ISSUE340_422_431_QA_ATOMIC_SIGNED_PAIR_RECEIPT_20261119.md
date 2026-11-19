# Issues 340/422/431: QA-only atomic signed-pair receipt staging

Date: 2026-11-19 (Europe/Kyiv)
Status: QA-only, non-deploying. No new signed release exists yet.

## Motivation

The sole-owner candidate supervisor now requires a root-controlled
paired receipt with a source tar SHA256, exact Git commit, OCI digest,
and complete source+OCI proof flags. Those flags in an arbitrary JSON
file are not signatures. A separate validated receipt producer must
establish the real source and OCI evidence first, then persist immutable
artifacts and finally publish a private receipt.

## QA-only stager

scripts/qa-stage-verified-pair-receipt.py supports:
- --dry-run: invoke independent signed-source/OCI read-only preflight
  using explicit version, exact 40-hex commit, OCI digest, TAR,
  source Sigstore bundle and SHA256SUMS. No files are created.
- --qa-stage: only root in the disconnected QA guest LXC114 named
  shelly-docker-reboot-qa may stage into the fixed private directory
  /var/lib/svt-qa-verified-pair-20261119. Reusing an existing
  directory is refused.

The stage copies the three input assets into an exclusive mode0700
private directory, each copy mode0600 with fsync. It verifies the
COPIED assets (never just the untrusted original paths) through
verify-signed-release-pair.py. That verifier proves exact signed
source release.yml identity and commit, signed immutable OCI image.yml
identity and amd64/arm64 revision plus SPDX/SLSA provenance.

Only after full verified source+OCI success does the stager create
the final deployed.json receipt with exact source tar SHA256, Git SHA,
immutable OCI digest and three explicit proof fields. It writes and
fsyncs a private temp file, creates the final receipt exclusively,
removes the temporary hard link, and fsyncs the directory. Receipt
is mode0600 and single-link on success.

If pair verification fails or the process stops before final commit,
the incomplete directory remains for inspection but no deployed.json
exists. The candidate supervisor must fail closed in this case.

## Checks performed

- 8/8 isolated staging tests PASS, including correct copy-before-verify
  order, exact receipt content, private modes, no prior receipt,
  invalid proof flags, mismatched Git SHA/digest, duplicate attempt
  refusal, symlink source rejection, wrong version and non-QA host.
- Actual read-only LXC114 CLI against the unsigned synthetic source
  TAR paired to the old v0.5.25-hw OCI digest returned BLOCKED,
  release_authorized=false and production_modified=false, without
  creating stage output.
- Joined networkless UID10001 read-only Docker regression 250/250
  PASS, including the newly added staging unit tests, signed pair
  identity, actual-archive guards, rollback and Pro3EM SQLite/fence.
- Ruff0.14.2 and project maintainability/quality gate PASS.

The positive staged signature proof in unit tests is MOCKED. It
is not evidence that a new release was cryptographically signed.

## Production release still blocked

This QA stager cannot stage on production and is not an installed
updater. Actual signed source+OCI new-release bytes and exact SHA
do not exist. An approved production receipt writer, new signed
image migration, true whole-history SQLite/WAL/PHOTO safety with
pending Telegram-intents, sole systemd owner and abrupt SIGKILL /
reboot rollback acceptance are not finished.

Never install this QA receipt into LXC110, run the legacy
apply-update.sh --recover, publish a release tag or enable the
candidate .unit based on synthetic fixture results.

## Commands

    python3 scripts/qa-stage-verified-pair-receipt.py --help
    python3 scripts/qa-stage-verified-pair-receipt.py --dry-run \
      --version <version> --commit <40-hex-sha> \
      --digest sha256:<64-hex> \
      --archive <absolute-tarball> \
      --bundle <absolute-sigstore-bundle> \
      --sha256sums <absolute-SHA256SUMS>
    python3 -m unittest -q test_qa_signed_pair_stager

The first two commands cannot install an image or write production.
The --qa-stage flag is restricted to the isolated LXC114 guest.
