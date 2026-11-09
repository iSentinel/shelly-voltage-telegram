# Fail closed on linked protected installation files

## Reproduced unsafe preflight

The safe install-target guard rejected a linked installation root and
symlinked parents, but accepted a root-owned install directory whose
`.env` or `secrets/` path was a symlink. A disposable preflight
fixture confirmed both variants were accepted with exit status 0.
Later installer steps chmod the environment, chmod the secrets
directory and touch/chown private secret files. On linked paths
those operations could affect files outside the installation root.

## Change

Keep existing installation target path validation and require a separate
protected-file guard before checking Docker writer state or installing
dependencies. Reject:
- symbolic links or non-regular paths for `.env` and managed secret files;
- symlinked or non-directory `secrets/`;
- hard-linked protected regular files, avoiding changes to shared inodes.

Absent files/directories remain valid for first installation.
Existing ordinary standalone protected files remain supported.
There is no access to file contents in these checks.

## Isolated verification

- RED: existing guard returned 0 for linked `.env` and `secrets/`.
- GREEN: 7/7 targeted installer path tests passed, including symlink
  and hardlink rejection and valid-file acceptance.
- Isolated Python 3.13: 1312/1312 tests passed.
- Ruff 0.14.2 format/lint and project quality gate passed.
- `bash -n scripts/install.sh` and `git diff --check` passed.

This branch is a draft stacked on PR #279 and does not authorize
merging, production update, Docker restart, SQLite backup, changes to
Telegram or access to Shelly device settings. Remaining predeploy
gates include independent source review, exact quiesced SQLite history
parity, YASNO protected recipient configuration, Telegram client
visual acceptance, and signed rollout/rollback approvals.

## GitHub CI fixture correction

GitHub Actions runs the fixture under sudo, but checkout is runner-owned.
The root-owned target preflight correctly blocks dry-run against checkout.
The in-place fixture now stages only VERSION and installer into a separate
root-owned disposable directory, preserving real in-place semantics and
the strict production safety check. No production behavior was relaxed.
