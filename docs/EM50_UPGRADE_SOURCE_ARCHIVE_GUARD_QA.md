# Verify source restore compatibility before installer changes

## Reproduced gap

The upgrade installer created a gzip source TAR and an SHA256 sidecar
without validating that the source was suitable for restoring the
application. A source tree containing only VERSION was accepted by
the snapshot phase, even though rollback rejects source archives
missing runtime essentials. That could let Docker/package and source
changes proceed with an unusable recovery point.

## Shared fail-closed validator

Both install.sh and rollback.sh now invoke the same
scripts/validate-source-archive.py. It requires all nonempty rollback
essentials, rejects duplicate, traversing, linked, special,
setuid/setgid or oversized TAR members, caps member count and declared
file bytes, then reads the full bounded gzip stream to check its
checksum and trailing data. The installer invokes this immediately
after source compression and before SQLite backup, Docker setup or
source replacement.

## RED / GREEN evidence

- RED: source-only VERSION snapshot returned success.
- GREEN: incomplete snapshot returns error 3 before installation.
- Existing snapshot acceptance 6/6 PASS after the synthetic
  install fixtures gained the actual required runtime paths.
- New source-validator acceptance 3/3 PASS: complete archive,
  missing app.py and invalid gzip trailer/checksum.
- Full rollback shell acceptance PASS, including source content,
  path safety, stopped writer and SQLite restore cases.
- Isolated Python 3.13 suite, Ruff, project quality and four
  installer dry-run scenarios are required before merge.

Draft source-only stacked on PR #289, with sibling PR #280 remaining
separate for integration verification. Never authorize release
without independent source review, quiesced complete SQLite/source
snapshot and restore acceptance, protected YASNO setup, real-client
Telegram approval and signed rollout/rollback. Live service unchanged.
