# Issues 340/422/431: full Python discovery QA diagnostics

Date: 2026-11-19. Scope: isolated QA LXC114, source-only.
Production LXC110 was NOT modified.

## First broad discovery

The initial full Python3.13 unittest discovery executed with:
- network none, UID10001, cap-drop ALL, no production credentials;
- read-only mounted candidate source and read-only container filesystem;
- 256MiB executable ephemeral /tmp;
- local, previously signed QA runtime image 0.5.21-hw without git;
- Python -W default; ResourceWarning explicitly counted.

Results: 2269 tests, 19 failures, 20 errors, 1 skipped,
ResourceWarning count 0. The full QA discovery FAILED. Do not
equate the 220/220 selected PASS with a green complete suite or
claim that full GitHub CI has been executed.

The major environment differences were the missing git executable,
too-small /tmp for a 768MiB backup-storage preflight, and the QA
image TELEGRAM_GRAPHIC_STYLE=v5 default while legacy graphic tests
assumed v4. The project CI uses a GitHub runner with git, real
disk space, current Python dependencies and separate style settings.

## Fixture regressions resolved

- A low-voltage dwell unit test mocked the global monotonic clock
  with only three values. The new writer transaction gate also
  consults this clock. The test now returns a stable extra clock
  value while still exercising the actual DB/fence/alert transition.
- A release archive fixture did not account for writer_fence.py and
  the new protected updater/supervisor scripts. It now includes
  those in synthetic source and checks writer_fence.py in the tar.
- Synthetic rollback source archives for graphic-rollout-readiness,
  graphic-cutover-snapshot and upgrade-snapshot uniqueness were
  missing writer_fence.py, which is now a mandatory safety module.
- The graphic-supply-topology test now explicitly selects v4 during
  its test case and restores the environment afterward; its source
  semantics and the production renderer were not changed.

Post-fix isolated QA:
- Release reproducibility and private-artifact exclusion shell
  fixture PASS; import-closure policy 4/4 PASS.
- Low-voltage dwell + import closure 5/5 PASS.
- Graphic rollback, snapshot, supply-topology, low-voltage and
  import-closure focused set: 73/73 PASS, ResourceWarning 0.
- Ruff0.14.2 and project quality/maintainability PASS.
- No production Docker, database, Telegram or GitHub release action.

## Remaining release gate

This is NOT a full-suite PASS yet. Reproduce full discovery with
an environment close to GitHub CI and do not weaken safety guards
or change production code only to make an underspecified QA image
pass. The exact-head GitHub CI matrix 3.11/3.12/3.13 remains
necessary, as do the actual signed updater/rollback transaction,
hard process interruption and exact source/OCI provenance proof.
The owner decision is recorded but no new image is deployed.
