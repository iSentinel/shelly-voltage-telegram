# Reject overlapping YASNO Docker mounts in prerelease preflight

## Reproduced bypass

The YASNO Docker reader preflight verified one exact read-only bind
from /var/lib/shelly-yasno-reader to /run/yasno, but did not inspect
other mounts affecting the same filesystem subtree. A second writable
mount at /run, /run/yasno/yasno.json, or /run/yasno/nested could
override the protected reader even though preflight returned PASS.

## Fail-closed guard

Require a valid Docker mount list, one exact approved read-only bind
at /run/yasno, and no other mount whose destination is an ancestor
or descendant of that protected directory. Reject malformed,
nonabsolute, dot-component or noncanonical mount destinations.
Independent non-overlapping mounts remain allowed.

This is a metadata-only check: no Docker changes, provider access,
cache reads in staging or Telegram messages.

## Evidence

- RED: three overlapping mounts were accepted by staging preflight.
- GREEN: all three now raise ValueError; unrelated /run/yasno-other
  mount remains accepted by staging.
- Focused preflight tests 15/15 PASS.
- Python 3.13 isolated full suite 1325/1325 PASS.
- Ruff 0.14.2 and project quality PASS; installer dry-run 4/4 PASS.

This is a Draft PR stacked on PR #286, with PR #280 independent
integration QA still required. Production 0.5.13-hw remains
unchanged. Before any release, require human source review,
stopped-writer complete SQLite/source snapshot, protected YASNO
producer/recipient setup, real-client Telegram approval and
signed deployment/rollback authorization.
