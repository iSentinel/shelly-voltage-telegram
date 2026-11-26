# Issues 340/422/431: sole-owner Compose startup candidate

Date: 2026-11-19. Status: SOURCE-ONLY; NO PRODUCTION DEPLOYMENT.

## Background

Production LXC110 still uses an independent Docker restart policy
unless-stopped, and its installed systemd unit invokes the old
apply-update.sh --recover path. That path can restore old SQLite
history after new three-phase readings have been written.

The candidate single-owner flow is root-protected fence provision,
Compose create without start, exact pinned image/receipt check,
supervised startup under the pending gate, then explicit controlled
stop before a newest-history WAL/PHOTO rescue.

## Source-only changes

- deploy/compose.supervised-candidate.yml overrides restart:no,
  configures SVT_WRITER_FENCE_DIR=/run/svt-fence and adds the
  root-owned shared /var/lib/shelly-voltage-fence bind mount.
- deploy/shelly-voltage-supervised.candidate.unit describes future
  sole-owner startup. Its .unit extension intentionally prevents
  automatic installation as an active .service by existing packaging.
- scripts/start-compose.sh --create-only requires an existing signed
  pin overlay and a trusted supervisor overlay. It calls Compose
  config then create --no-build --no-recreate and never up.
- Setting SVT_SUPERVISOR_OVERLAY forbids ordinary Compose up or
  --build, so the script cannot bypass the sole-owner path.
- Signed pin, supervisor and optional read-only YASNO layering are
  ordered deterministically; YASNO remains the final layer.

## Evidence from isolated LXC114

- Synthetic Docker Compose config: restart=no, protected fence mount,
  preserved /data, immutable-shaped image digest, read-only rootfs.
- Candidate systemd syntax verified by systemd-analyze using a
  disposable .service copy; no service was installed/enabled.
- 19/19 start-compose unit tests passed, including pin and supervisor
  requirements, create-only no-up, overlay-order, symlink rejection
  and ordinary unsupervised start refusal.
- A real networkless, nonroot QA Compose create produced a stopped
  container with Docker restart policy no. An independent supervisor
  --once then returned start success.
- The more extensive synthetic Pro3EM application boot test DID NOT
  pass: QA worker exited 1 before tables or measurements appeared.
  Joined L1/L2/L3 acceptance must therefore stay BLOCKED.
- A Gateway SSH timeout temporarily interrupted the diagnostics;
  connectivity was later restored.

## Release blockers

This candidate does not yet integrate the real signed updater and
rollback, exact version-paired OCI/source receipt, full latest WAL
and PHOTO history restoration, nor a completed joined crash/reboot
test. The source-only unit must NOT be enabled together with the
currently active production unit. No source/image cutover, GitHub
push or Telegram action is approved. Keep LXC110 unchanged while
electricity is OFF and the meter is unavailable.

## Follow-up: bypass prevention

Source-only follow-up ensures that scripts sourcing
start-compose.sh cannot call compose_cmd up while the supervisor
overlay is selected; direct up now returns 3 before invoking Docker.
The candidate supervisor also refuses to start if Docker reports
the chosen container is already running, so systemd cannot silently
adopt a foreign writer.

Checks: 27 targeted source-only unit tests PASS; 168 joined selected
tests PASS on isolated LXC114 Docker; Ruff 0.14.2 and maintainability
PASS. The separate real Compose-created Pro3EM worker failure remains
unresolved and cannot be counted as a passing boot acceptance.

## Signed receipt/source version pairing, 2026-11-19

The source-only supervisor now requires a root-controlled
RELEASE_VERSION marker under protected parent directories and a
matching version field in the root-private deployed.json receipt.
The receipt version must conform to the supported immutable release
format, and its OCI sha256 digest must match the image configured
on the already-created, stopped Docker container.

This is only a *local identity consistency* check: it does not
verify the Cosign signature itself, attest an entire source tree,
nor authorize a deployment, update or rollback.

Real isolated root-owned file QA on LXC114:
- Exact 0600 source marker / 0600 private receipt / pinned image
  matched successfully, followed by a successful repeat after
  restoring original files.
- Wrong source version, substituted image digest, group/world
  writable source marker, exposed receipt and symlinked marker
  were all rejected before Docker start.
- New supervisor policy unit tests 11/11 PASS.
- 172 selected combined offline QA regression tests PASS in
  networkless nonroot Docker, Ruff0.14.2 and maintainability PASS.
- Real production source marker and receipt versions were examined
  read-only, both at 0.5.25-hw with 0600 root-owned permissions.
- Production remained offline, old image and systemd unchanged.

The next acceptance gate remains real joined Compose-created
three-phase writer startup, then signed old/new OCI/source promotion
and crash-safe newest WAL/PHOTO recovery under one update fence.

## Joined QA writer boot accepted — 2026-11-19

The previously unresolved application failure was caused by the
*test harness*, not by the joined application: its polling connection
called ordinary sqlite3.connect() before the writer had created the
database, creating a root-owned empty file. The unprivileged UID10001
worker then received "attempt to write a readonly database" at WAL
initialization. The test also initially imported the old /app/app.py
instead of the source under /src. Both errors were fixed in the
disposable QA harness: working_dir=/src and read-only SQLite URI
mode=ro for the observer.

The reusable source-only tests/qa_compose_supervisor_runtime.sh
requires the exact disposable LXC114 hostname and root, has --help,
uses no network and no real Shelly or Telegram credentials, builds
only synthetic 3-phase readings, and cleans its container and files.

A real LXC114 joined acceptance now PASS:
- Shared root-controlled fence preflight succeeded.
- Compose create-only produced a STOPPED container with restart=no.
- Sole supervisor started the source runtime; L1/L2/L3 committed
  with equal row counts and SQLite quick_check=ok.
- Root published a pending marker. The supervisor refused startup.
- Root cleared the exact token. Sole supervisor restarted the writer.
- Total history grew from 3 to 102 records, with complete equal
  phase counts, preserved Telegram PHOTO marker, and quick_check=ok.
- Temporary Docker container was removed, private test directory
  deleted and root marker cleared.

This closes only the synthetic *runtime QA acceptance* blocker.
It does NOT validate the production signed image, updater/rollback
cutover, Cosign cryptographic verification or live service startup.
No production changes or new image publication occurred.

## Source-only dual-owner cutover protection

The candidate systemd unit now declares
Conflicts=shelly-voltage-compose.service and orders itself after the
old service in the systemd dependency graph. This is a defensive
blueprint only: installing/enabling the unit before verifying that
the original service is disabled remains prohibited.

Five structural policy tests and disposable systemd-analyze syntax
verification PASS on LXC114. The complete selected offline test
suite is 182/182 PASS, Ruff0.14.2 and maintainability PASS.
QA-only source release archive SHA256SUMS and included guarded
components PASS. No production systemd unit was installed, loaded,
restarted or enabled. No signed OCI release was created.

Before an Owner Approval can authorize a production cutover, the
actual apply-update.sh/rollback.sh must participate in the same
durable pending/owner/newest-WAL-and-PHOTO transaction, and signed
old/new source/OCI pairs must be verified against exact source
version and digest. Both startup and rollback must survive real
SIGKILL/reboot boundaries with complete L1/L2/L3 data parity.
