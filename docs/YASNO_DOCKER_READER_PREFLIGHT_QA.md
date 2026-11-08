# YASNO Docker reader mount: read-only release gate (QA only)

This additional acceptance check prevents a correct YASNO host cache from
being mistaken for a ready Telegram integration when Docker has not mounted
the recipient data. It does not start, stop, recreate or update containers.

The production bot remains 0.5.13-hw. Its YASNO reader mount is not yet
installed. This script is source-only prerelease work for 0.5.14-hw.

## Two safe rollout phases

**staging** (feature DISABLED): require a running hardened Docker
configuration with user 10001:10001, read-only root filesystem,
no privileged flag and CAP_DROP=ALL. Require exactly one
read-only bind mount from /var/lib/shelly-yasno-reader to /run/yasno,
plus a trusted file setting for /run/yasno/yasno.json, YASNO_GROUP=10.1
and TELEGRAM_YASNO_STATUS_ENABLED=0. Staging may happen only after an
explicitly approved container recreation. It cannot publish YASNO yet.

**active** (feature ENABLED): all the staging constraints and the exact
TELEGRAM_YASNO_STATUS_ENABLED=1 flag. Additionally, the recipient
host cache must have protected root:10001 permissions and pass pinned
provider metadata, integrity, Kyiv publication date, capture freshness
and full-day slot validation. Default --phase is active, fail-closed.

## Commands, read-only and currently expected to FAIL for production

    python3 scripts/yasno-docker-reader-preflight.py --help
    python3 scripts/yasno-docker-reader-preflight.py --dry-run
    python3 scripts/yasno-docker-reader-preflight.py --check --phase staging
    python3 scripts/yasno-docker-reader-preflight.py --check --phase active

The --check operation calls Docker inspect locally and reads only the
needed metadata in memory. It never prints raw environment variables,
tokens, IDs, inspect JSON or secret paths.

For offline tests without Docker, use --inspect-fixture on a synthetic
Docker inspect JSON. Do not save production inspect JSON for testing:
it can contain sensitive environment variables.

The existing deploy/compose.yasno-reader.example.yml provides a reviewed
*disabled-by-default* example. The independent
scripts/yasno-systemd-account-preflight.py --check --phase bootstrap|live
validates the actual host service account and cache directory ownership.
All checks must pass BEFORE reviewed activation. No script in this QA
stage creates users, installs timer files, binds volumes or sends Telegram.

## Accepted / blocked

- Live Gateway: expected FAIL for staging and active because production
  does not yet have the reader bind.
- Synthetic staging: only exact nonroot read-only mount and disabled
  feature are PASS.
- Synthetic active: checks the real protected cache validator; no
  stale, malformed or wrong-source schedule may pass.
- Production remains unchanged during the electricity outage.

The private Telegram send/edit was accepted visually by the operator in
an earlier step. Final rollout still requires a secure service account,
reviewed mount, approved restart, scoped PR review, signing and
rollback permission. No release is authorized by this check alone.

## Regression

    PYTHONPATH=.:tests python3 -W default -m unittest tests/test_yasno_docker_reader_preflight.py -v
    PYTHONPATH=.:tests python3 -W default -m unittest discover -s tests -q
    scripts/quality.sh
    bash tests/test_release.sh
