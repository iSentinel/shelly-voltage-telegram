# Validate rollback application target before stopping the writer

## Reproduced P0 risk

Rollback validated snapshots, SQLite and rescue paths before stopping
Docker but did not validate SVT_ROOT, which is later used by the source
restore's recursive cleanup. A synthetic dry-run accepted SVT_ROOT
pointing through a symlink to an unrelated directory (RED).
In a real rollback that path could redirect the destructive source
replacement outside the intended installation.

## Fail-closed preflight

Before archive reads or Docker stop, require the application root:
- to be an explicit absolute path, not /, not a relative path;
- to have no empty, dot or dot-dot components or trailing separator;
- to contain no symlinks in any parent or final path component;
- to be an existing directory owned by the privileged operator.

An unsafe target returns exit code 3 with a generic error, without
touching source files, SQLite, Docker or Telegram.

The isolated rollback fixture checks aliases to the target itself
and to its parent, root and relative paths, traversal and an absent
target. Both target VERSION SHA and live test SQLite SHA remain
unchanged. The ordinary rollback success path is still covered.

This is source-only Draft QA. No production stop, rollback, archive
capture, database mutation, Shelly action or Telegram send/edit.
The release remains gated on full-history predeploy SQLite parity,
independent source review, visual acceptance and signed approval.
