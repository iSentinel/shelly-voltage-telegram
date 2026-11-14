# Read-only SQLite schema compatibility preflight

**QA-only for Issue #340. This is not an automatic boot or source
installation authorization.**

## Why this guard is needed

The released application stores its schema version in the SQLite
meta table, key schema_version. It does **not** use SQLite header
PRAGMA user_version as the source of truth. On the latest production
read-only observation, user_version was 0 while meta.schema_version
was 3. The staged release v0.5.21-hw also declares SCHEMA_VERSION=3.

A signed OCI digest does not by itself prove that a writer can safely
use or migrate the current database. A boot continuation must never
downgrade newer schema history or silently invoke migrations.

## Candidate behavior

This standalone audit parses the literal top-level SCHEMA_VERSION
assignment with Python AST, without importing or executing app.py.
It checks two matching release version marker files, each regular,
bounded, nonlinked and not writable by group/others.

The supplied current SQLite file is opened with mode=ro and
PRAGMA query_only=ON. The audit checks quick_check=ok, the
application's meta.schema_version, historical measurements,
the required five tables, named columns and release v3 indexes.
Extra application tables are permitted as observations; they do
not authorize a migration. The header user_version is reported
separately and never substituted for meta.schema_version.

Exact schema version + structural parity gives only
SCHEMA_OBSERVED_MATCH, **not** proven runtime compatibility.
A future DB version is rejected as a downgrade risk, while an older
DB version is marked as requiring independently reviewed migration.
A missing column/index, missing history or unsafe input cannot
grant boot authority.

Every outcome keeps these fields false:

- schema_migration_executed
- signature_verified_here
- schema_runtime_compatibility_proven
- boot_start_authorized
- source_install_authorized
- telegram_photo_authorized
- production_modified

This script does not verify a Cosign signature, contact Docker or
Telegram, create an SQLite backup or run migrations. It is only one
conservative input to a future, owner-reviewed boot policy.

## Usage

Inert default:

    python3 scripts/readonly-schema-compatibility-preflight.py --dry-run

Synthetic or staged release source and one existing SQLite:

    python3 scripts/readonly-schema-compatibility-preflight.py \
        --source /private/qa/release \
        --database /private/qa/history.sqlite3 --jsonl

Use -h or --help. Both source and database paths are mandatory for
a real read-only check. A missing argument does not fall back to
production. A successful observation returns 0; REVIEW_REQUIRED or
BLOCKED returns 2.

## QA acceptance (2026-11-14)

- 27/27 synthetic focused unit tests PASS, full Ruff quality PASS.
- Positive source v0.5.21-hw + application schema v3 and matching
  existing SQLite v3 gives SCHEMA_OBSERVED_MATCH without a write.
- Future schema version, older schema version, missing required
  field/index, empty history, corrupt marker, malformed source AST,
  symlink/writable source or missing/corrupt DB are rejected safely.
- No source code is imported: a malicious computed SCHEMA_VERSION
  expression is parsed, not executed.
- Full project source archive validator PASS; functions <=40 lines.
- Staged v0.5.21-hw release source against production SQLite in
  explicit read-only mode reported observed schema version 3/3,
  user_version=0, all required tables/indexes present, 64k+
  measurements and no schema migration executed.

## Limits and production gates

The staged release v0.5.21-hw is not installed as host source: the
actual host source remains v0.5.15-hw. This check alone does not
prove the staged release source matches the exact signed OCI bytes,
that every dynamic application query succeeds, or that a migration
can run safely on current history.

An independent Cosign verification of the currently running signed
image passed separately, but this preflight does not invoke Cosign.

Issue #340 remains BOOT CONTINUATION NO-GO pending full writer/DB
compatibility on a nonproduction copy, verified immutable signed
image source parity, all restart-owner fencing, exact current
source+whole SQLite A/B, physical power-loss acceptance and explicit
owner approval. Telegram TEXT-to-PHOTO remains NO-GO.
