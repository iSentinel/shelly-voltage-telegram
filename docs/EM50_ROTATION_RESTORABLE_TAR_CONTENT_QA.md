# Count only restorable source archives in upgrade retention

## Reproduced data-retention gap

The previously hardened snapshot rotation checked SHA256, gzip integrity,
that a TAR listed successfully, and verified the matching SQLite archive.
A gzip TAR containing only VERSION still passed all those checks, although
rollback cannot restore a working application from it. This made the
complete-looking but unrestorable folder count against the five retained
good backups.

An isolated fixture with six genuine upgrade snapshots and a newer
VERSION-only source TAR (valid gzip CRC, filename-bound matching SHA256,
valid SQLite gzip and manifest.env) reproduced RED: only **four** usable
source+SQLite pairs remained instead of five.

## Fix

Call the same fail-closed source validator used by the install/rollback
hardening: scripts/validate-source-archive.py. It checks required non-empty
runtime files, member types and paths, duplicate entries, size limits,
and gzip checksum. A candidate must pass both source and SQLite archive
validators before counting toward the retention limit.

Unverifiable and unrelated directories remain untouched and are not
counted. Fresh snapshots created by the ordinary upgrade process have
all mandatory source files. The validator is deliberately identical
to the already-reviewed source validator in cumulative PR #291/#290;
the separate PR branches must be combined and reviewed before merge.

## Independent acceptance

- RED: VERSION-only valid gzip TAR displaced a genuine backup (4 vs 5).
- GREEN: five genuine snapshots retained, fake folder preserved.
- Focused retention tests: 7/7 PASS.
- Standalone isolated Python 3.13: 1312/1312 PASS.
- Ruff 0.14.2, quality gate, four installer dry-run fixtures,
  shell syntax, and full rollback shell tests PASS.
- Disposable cumulative PR #291 + this branch requires separate
  integrated acceptance and GitHub CI validation.

No production archive was rotated, copied or modified.
No Docker restart, SQLite write, YASNO activation, Telegram edit/send
or Shelly RPC was made. Release remains blocked by independent human
source review, quiesced full-history SQLite/source rescue snapshot,
protected YASNO account/reader, Telegram real-client visual acceptance
and signed deployment/rollback permission.
