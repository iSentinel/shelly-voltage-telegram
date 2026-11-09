# Docker image and source release acceptance — QA

This QA stage fixes an important release risk: source-level
unit tests passed while the Dockerfile copied only an obsolete
subset of the Python modules imported by app.py.

## Runtime packaging

The Dockerfile copies all root-level Python source modules,
the pinned requirements and VERSION. It preserves the
non-root container user 10001:10001. No secret files, .env,
local databases, or user directories enter the image.

The GitHub CI builds the real image and then runs an
import test inside it with --network none:

    docker run --rm --network none --entrypoint python shelly-voltage-telegram:ci -c "import app; import yasno_cache; import telegram_channel_yasno"

A missing module now fails CI. This is still not a live
Telegram delivery test or a production deployment.

## Reproducible release checks

The required source files in scripts/release.sh include
the full root module dependency chain. Its privacy fixture
has the matching files but does not include any private
local .env values. The source release archive remains
reproducible and produces SHA256 manifests.

Static tests/test_container_package.py walks the local
Python import graph with AST and checks that package
and CI smoke guards cannot silently regress.

## QA commands

    scripts/quality.sh
    PYTHONPATH=.:tests python3 -m unittest tests/test_container_package.py -v
    PYTHONPATH=.:tests python3 -m unittest discover -s tests -q
    scripts/release.sh --dry-run
    bash tests/test_release.sh

Local QA result: 1088/1088 tests PASS; Ruff and quality
PASS, source release reproducibility and .env privacy
PASS, dry-run PASS. Image-level CI still needs its
independent GitHub runner outcome.

## Release gates

VERSION remains the QA baseline 0.5.13-hw; do not
modify LIVE_VERSION or publish a signed release merely
because source tests succeed. A versioned EM-50-only
release candidate requires independent image smoke,
approved private Telegram recipient checks,
the protected YASNO cache mount/reader identity,
a validated backup and rollback, and explicit approval.
