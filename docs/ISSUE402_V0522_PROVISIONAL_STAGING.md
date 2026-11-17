# Issue #402 — provisional 0.5.22-hw candidate (NOT RELEASED)

**Source-only staging proposal, 2026-11-17. No owner approval for
this exact candidate, tag, OCI or production activation is implied.**

## Scope and version markers

- Development candidate `VERSION=0.5.22-hw`.
- Last accepted publication `RELEASE_VERSION=0.5.21-hw` is **unchanged**.
- Historical `LIVE_VERSION` is unchanged.
- At the time of staging, GitHub `main` and `v0.5.21-hw` both
  contained `VERSION=0.5.21-hw`; the `v0.5.22-hw` ref was not found.
- The candidate includes the previously QA-tested durable Telegram
  text-send intent and owner-only single-NVMe risk audit changes.
- The sole owner explicitly accepted catastrophic one-NVMe data-loss
  risk and sole-owner code-review governance. No second physical device
  or independent reviewer will be required.
- The exact source commit, image digest and deployment/rollback plan
  still require separate owner acceptance.

## Fail-closed tag policy (testable now)

While `VERSION` differs from `RELEASE_VERSION`, the stable-tag
publisher must reject **both** old and proposed release tags:

```bash
python3 scripts/image_release_policy.py \
  --event push --ref refs/tags/v0.5.21-hw --root .
python3 scripts/image_release_policy.py \
  --event push --ref refs/tags/v0.5.22-hw --root .
```

Both commands must return **exit 1**. Do not change the stable marker
or create `v0.5.22-hw` merely to make the checks pass. The eventual
stable tag, release marker, immutable signed OCI and source commit
must match exactly and have specific Owner Approval.

## Local dry-run and package QA

```bash
./scripts/release.sh --dry-run
python3 -m unittest discover -s tests \
  -p test_em50_release_candidate.py -q
python3 -m unittest discover -s tests \
  -p test_image_release_policy.py -q
```

For an offline archive rehearsal, run `release.sh` only in an isolated
QA copy with two empty disposable output directories. Compare both
archive hashes using an identical `SOURCE_DATE_EPOCH`, verify its
SHA256 manifest and inspect the archive for private paths. Do not
publish it as an official release or upload it to the deployment host.

## Production remains unchanged

Live production may continue to run `0.5.21-hw` while Shelly is
online. The old on-host checkout `0.5.15-hw`, updater pin and emergency
image digest are known to disagree; never silently overwrite source
or edit `deployed.json` to disguise the inconsistency.

A fresh WAL-consistent local SQLite prechange backup/restore, exact
signed OCI, single-writer boot/rollback rehearsals, private recipient-
visible Telegram send/edit acceptance, approved source review and
specific owner activation authorization remain separate prerequisites.
PHOTO status additionally needs specific opt-in.

**Cutover:** NO-GO. **PHOTO:** NO-GO. **Published OCI:** none.
