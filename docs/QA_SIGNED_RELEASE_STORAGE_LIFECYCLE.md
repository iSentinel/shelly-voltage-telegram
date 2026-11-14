# Signed-release-source Storage lifecycle acceptance

**QA-only. Issue #340. No production data, network, Docker or Telegram.**

## Scope

This acceptance exercises the **real** app.Storage class and methods
from the prepared v0.5.21-hw release source, not a fake Storage
implementation. The release app.py in the staged verified source
archive and the checked-out repository had identical SHA-256:

    a13ffc9620e743f11cd997bc418ba754a9f8baa4b6c6e9d7174822af7b31bc8e

An independently executed Cosign v3.0.6 check previously validated
the running immutable v0.5.21-hw OCI release identity. This source
SHA comparison **does not** independently prove that the staging
file is byte-identical to app.py inside that image. The installed
host source remains v0.5.15-hw. All production changes remain NO-GO.

The tests import app.py solely for its Storage API. The real Shelly
monitor process is never started. The default class under test is
the local source tree. The optional SVT_APP_PATH environment variable
lets an operator explicitly target an independently staged release.

Every SQLite fixture uses a private temporary directory and invented
samples, timestamps, outage entries and metadata. The suite patches
socket connect to reject any accidental network access. It never
reads the installed production SQLite database or secrets.

## Tests

1. **Full-history idempotency:** generate 65,000 synthetic voltage
   samples, then call actual Storage.initialize twice. Compare a
   complete logical SQLite dump before and after. Preserve outage
   history and private test metadata.
2. **Actual writer API:** use actual Storage.add on the populated
   65,000-row DB; verify one new sample, original row and outage are
   preserved, with schema_version=3.
3. **Future schema:** mark a synthetic DB as meta.schema_version=4;
   actual Storage.initialize must refuse it without changing any
   logical SQLite history.
4. **Legacy migration:** create a real v2-shaped fixture, call actual
   Storage.initialize to migrate it to v3, and verify both original
   measurement and outage edges and default L1 phase survive.
5. **SIGKILL with WAL:** a separate synthetic SQLite writer commits
   one sample and leaves one uncommitted; kill only that disposable
   subprocess with SIGKILL. Reopen using actual Storage.initialize,
   confirm that only the committed row survives, and append another
   sample through actual Storage.add.
6. **No network:** real Storage initialization and insertion work
   with socket connections rejected.

## Command

Use an isolated Python environment with the release requirements.
The suite uses unittest, exposes ordinary unittest -h/--help, and
does not have an installer or an automatic production fallback.

    python3 -m unittest discover -s tests \
        -p test_signed_release_storage_lifecycle.py -v

To explicitly verify the prepared release source:

    SVT_APP_PATH=/private/qa/verified-v0.5.21-hw/app.py \
    PYTHONPATH=/private/qa/verified-v0.5.21-hw \
    python3 -m unittest discover -s tests \
        -p test_signed_release_storage_lifecycle.py -v

No command in this document starts the live container or performs
production installation.

## Acceptance on November 14, 2026

- Exact staged v0.5.21-hw app.py and repository app.py SHA-256 match.
- **6/6 synthetic actual-Storage lifecycle tests PASS** against
  each source tree, including 65,000-record preservation and the
  committed/uncommitted SIGKILL WAL distinction.
- Source-tree full Ruff quality gate PASS, PEP8/function <=40 lines.
- No source installation, network connection, Telegram call,
  production SQLite read/write or Docker restart.

## Remaining NO-GO

This does **not** prove 65,000 real records are compatible with a
specific release, the full running OCI application behavior, or
data durability during physical power loss. It does not exercise
an actual Telegram writer reboot under all restart-owner conditions.

Issue #340 still needs a separately isolated signed-image writer
replica, real restart-owner boot orchestration, independently
approved current-history source+SQLite A/B, physical storage
failure acceptance and explicit owner approval. The Telegram public
TEXT-to-PHOTO cutover and installed-source reapply remain NO-GO.
