# Nested private-directory containment in public releases

## Scope

The public source release uses a separate tar staging path from
`scripts/source-tar.sh`. Before this fix, only root-level `secrets`,
`validation` and `dist-publish` directories were explicitly excluded by
the tar builder. The staged-tree check also matched only root-level
paths, so a nested private directory could bypass the intended policy.

## Defense in depth

- Exclude any-depth directories named `secrets`, `validation`, `dist`,
  `dist-audit`, `dist-publish`, `.work-state`, `.git`,
  `__pycache__`, `.ruff_cache`, `.pytest_cache`, `.mypy_cache`.
- Verify the staged tree contains no forbidden directories, even when
  an exclusion rule or archive creation behavior changes.
- Continue to reject symlinks and generated/private files.
- Preserve reproducible release output for valid public sources.

The synthetic test adds `nested/deeper/secrets/notes.txt`,
`nested/deeper/validation/evidence.json`,
`nested/deeper/dist-publish/output.tar.gz`, and
`nested/deeper/.work-state/checkpoint.json` to a temporary fixture.
It asserts none is present in the public archive. No real secrets,
Telegram IDs, or production database are used.

## Release status

This is source-only QA. A successful GitHub CI run or generated
release archive does not authorize deployment. The actual Shelly
writer, current SQLite backup parity, independent review, Telegram
visual sign-off and explicit rollout/rollback approval remain separate
production gates.
