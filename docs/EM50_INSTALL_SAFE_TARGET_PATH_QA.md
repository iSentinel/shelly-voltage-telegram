# Validate installer source destination before any side effects

## Reproduced issue

The release and direct installers accepted an operator-controlled
SVT_ROOT without checking for links, traversal or the filesystem
root. A synthetic --dry-run successfully accepted "/", ".",
a linked destination, a linked parent and a dot-dot traversal path.
A real invocation could redirect source installation and overwrite
files outside the intended application root.

## Fail-closed fix

Before checking Docker quiescence, installing packages, snapshotting
SQLite or copying source files, validate the destination is an
explicit absolute path (not "/"), without empty path components,
dot/dot-dot traversal or trailing separators. Check the final
directory and every existing ancestor for symlinks. Reject an
existing non-directory or a target owned by another user.

Fresh installs at a missing leaf remain possible. A valid existing
directory owned by the invoking operator also passes, provided
the separately established Docker writer guard permits the upgrade.

## Regression

Source installer functions and invoke the full main --dry-run with only the root privilege check stubbed, using disposable targets. This works on unprivileged CI runners.
Reject linked final directories, linked parents, filesystem root,
relative and traversal paths, existing regular-file destinations
and trailing separators. Confirm normal existing and fresh target
dry-runs pass without writing files. Ensure main() calls the path
guard before the existing Docker-writer gate.

This source-only hardening does not authorize promotion, stop the
production bot, capture a live backup, or change Telegram or Shelly.
