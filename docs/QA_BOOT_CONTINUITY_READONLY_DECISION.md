# Read-only boot continuity decision — no startup authorization

**Issue #340. QA-only and observation-only. This tool never starts a
container, repairs installed sources, invokes live recovery, mutates
SQLite or authorizes a Telegram status change.**

## Context

Production currently runs signed-image release v0.5.21-hw, while
installed host sources remain v0.5.15-hw. A prior production source
installation was explicitly denied; the denial remains binding.

The installed Compose service executes apply-update.sh --recover in
ExecStartPre before invoking start-compose.sh. If an interrupted
update has an older SQLite archive, recovery must fail closed to
preserve recent data. But a failed ExecStartPre prevents the main
Compose service startup. This protects history, not boot availability.

The normal startup also has an unsafe fallback for any future
automatic recovery policy: without the private image override it
can execute Docker Compose up without --no-build, against a base
manifest containing build: . An automatic recovery must never silently
fall back to a local build instead of a signed immutable image.

## Scope

The candidate reads the private deployed receipt, image override,
installed source markers, current SQLite and optionally the current
Docker container identity (read-only inspect).

It requires state mode 0700 and owner identity; private regular
0600 receipt and override; immutable image/receipt parity; valid
non-linked pending marker where present; SQLite quick_check=ok,
at least one historical measurement and a readable outage table.

No private value or secret is printed. The runtime image is obtained
by Docker inspect only. A SHA256 image reference is an identity and
does **not** prove a cryptographic signature. This script does not
run Cosign and always reports signature_verified_here=false.

All outcomes set boot_start_authorized=false,
docker_build_allowed=false, source_install_authorized=false,
telegram_photo_authorized=false, production_modified=false.
Nothing produced by the audit can grant a live start/installation.

## Commands

Inert mode, no host access:

    python3 scripts/boot-continuity-decision-audit.py --dry-run

Explicit read-only actual state:

    python3 scripts/boot-continuity-decision-audit.py \
        --live-readonly --jsonl

Synthetic fixtures:

    python3 scripts/boot-continuity-decision-audit.py \
        --runtime-fixture /private/qa/runtime.json \
        --root /private/qa/source \
        --state /private/qa/state \
        --database /private/qa/history.sqlite3 --jsonl

Use -h or --help. The fixture mode does not fall back to Docker.

Exit status 0 means only a consistent observation of an already
running healthy pinned container, with no pending journal or source
version mismatch. It never authorizes a new boot, release, signature
or restart-owner fence. REVIEW_REQUIRED or BLOCKED returns exit 2.

## Isolated QA acceptance

- 30/30 synthetic unit tests, full Ruff quality and filtered source
  archive validation PASS.
- An exactly matching running image is observed without conferring
  any cryptographic signature or boot permission.
- Missing, writable, symlinked, malformed or unpinned deployment
  state fails closed. All blocked reports explicitly deny boot, build,
source installation and Telegram PHOTO, including missing fixtures.
- Corrupt, missing, relative-path or parent-symlinked SQLite fails closed.
- Pending marker, source-version mismatch, stopped or unhealthy
  writer, current open outage and image mismatch demand review.
- Fixture mode never invokes live Docker; default mode reads no
  files. Read-only runtime mode only invokes docker inspect.
- Live LXC110 reports REVIEW_REQUIRED because installed sources
  0.5.15-hw differ from the deployed v0.5.21-hw receipt. The private
  selected pin matches current Docker image, but no signature
  verification takes place here.

## Production NO_GO and follow-ups

A separate independently reviewed design must decide how systemd
responds to stale pending recovery on real boot, preserving all
current SQLite/WAL, pending journal and diagnostics. Any approved
continuation must verify immutable signed image identity with a
trusted verifier, check database schema compatibility and prevent
unexpected local build fallback.

Real Docker restart policy, enabled Compose service and updater
timer, boot hook, other restart owners and actual host reboot/power
loss remain unresolved. The exact fresh writer-fenced SQLite and
signed source A/B and explicit owner authorization are required
before live maintenance. No Telegram TEXT-to-PHOTO change is
authorized by these QA checks.
