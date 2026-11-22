# Issues 340/422/431: Source Release exact-head CI and rollback fixture

Date: 2026-11-19. State: QA branch only; no production deployment.

## Independent GitHub CI findings

The first GitHub CI run for branch qa/issue340-422-431-20261119,
SHA 303290faa893cee6ee1a48c08bbb7d339233cd3c, was
https://github.com/iSentinel/shelly-voltage-telegram/actions/runs/44493439068.

The resolve job passed. Python3.11 completed unit tests and shell
syntax and initial installer dry-run, but failed at the Rollback fixture
step. Other matrix jobs were cancelled. That run did NOT pass.

The failure was reproduced independently on isolated LXC114. Its
tests/test_rollback.sh built a supposedly valid source archive
without writer_fence.py, which had become mandatory for a safe
rollback archive. It reached "rollback source archive is invalid"
before the test could assert that unsafe rescue paths were rejected.

The fixture now includes writer_fence.py in synthetic OLD_SRC and
in the list of individually missing mandatory archive members.
The full tests/test_rollback.sh then passed on LXC114, covering
private SQLite restore, source and DB integrity checks, hardlink
and symlink rejection, stopped-writer guard and Telegram status
race restrictions. No live rollback was executed.

## Source Release is now CI-gated

The Image workflow already gates image build/signing on the
reusable CI matrix at the exact checkout SHA. The Source Release
workflow previously lacked this same requirement.

The proposed .github/workflows/release.yml now:
- runs .github/workflows/ci.yml with the selected immutable tag ref;
- requires successful CI before the source-release job starts;
- checks that the exact checked-out tag SHA equals CI-tested SHA;
- requires the release tag, VERSION and RELEASE_VERSION to agree;
- publishes only after source archive and SHA256 safety checks.

This closes a split where source archives and signed OCI images
could be published after testing different versions or commits.
No release tag, source asset or GHCR image was published.

QA evidence: 26/26 workflow and security policy unit tests PASS,
independent YAML parser PASS, entire shell rollback fixture PASS,
Ruff0.14.2 and maintainability PASS on LXC114.

## Remaining blockers

Push the new QA commit and obtain a successful full
Python3.11/3.12/3.13 GitHub Actions matrix. Real signed old/new
source+OCI pairing, newest WAL/PHOTO safe actual updater/rollback
transaction, hard updater-process interruption and exact release
manifest are still necessary before production promotion.
Owner Approval was recorded, but that is not an authorization to
skip any of these technical gates.
