# Issue #431 — atomic Pro3EM polling-cycle persistence

Status: SOURCE-ONLY QA. No production deployment, merge or release
authorization. Related: Issue #340 one restart owner/shared SQLite
writer fence; Issue #422 signed OCI/source/WAL rollback.

## Reproduced defect in signed v0.5.25-hw

The original Monitor.poll_pro3em_once() reads all three phases
from one EM.GetStatus, then calls accept_phase_sample() for
L1/L2/L3 sequentially. Each call invokes Storage.add(), opening
and committing a separate SQLite transaction.

A separate disposable container running the EXACT currently
signed v0.5.25-hw image was externally SIGKILLed just after one
real Storage.add(L1) commit and before L2/L3. Docker exit 137;
isolated SQLite quick_check=ok but only L1=1 was present.
No hardware, production database, Telegram, or secrets touched.

## Source-only fix under QA

Storage.add_phases(ts, readings) now inserts the available
phase rows through one executemany() inside one SQLite
transaction. The Pro3EM poll reads the existing single RPC
response, selects only L1/L2/L3, calls add_phases() exactly
once, then performs the existing per-phase state/alert callbacks
with persisted=True. Thus callbacks cannot double-insert rows.

The legacy Storage.add() and direct accept_phase_sample()
remain intact for a one-phase meter, out-of-band secondary
sampling and other accepted callers.

When an RPC response lacks L2 or L3, only genuinely available
phase readings are committed together. The missing phase
continues to run the existing explicit per-phase failure path:
no fabricated 0 V, no reused cached value and no change to
the low-voltage thresholds or Telegram destination.

**Important scope:** whole-RPC triplet persistence is atomic
for available phases. The code intentionally does NOT demand
that a legitimate partial reply contain all three phases,
because that would silently discard valid measurements.
A partial database timestamp is a real partial reading, not
evidence of a half-committed transaction. Monitor state
continues to distinguish which phase was missing.

## Independent validation

Within the already signed v0.5.25-hw image, mounted the QA
source under /src and ran with working directory /src.
That detail matters: running in the default /app would load
the unmodified signed image source, without add_phases().

All containers were nonroot UID10001, read-only rootfs,
--network none, dropped capabilities, without secrets or
production mounts. Only a disposable synthetic SQLite folder
was writable.

- 8/8 new source-only Storage/Monitor tests PASS: complete
  timestamp triplet, forced SQL abort at L3 rolls back all
  phases, real missing L2 retains L1/L3, empty response has
  zero writes, old L1 API preserved, no duplicate writes,
  missing state handled, and failed batch changes no
  phase runtime state.
- Selected existing regressions PASS: test_app.py 154/154,
  test_phase_readiness_and_rollup.py 6/6,
  test_rpc_nonfinite_samples.py 11/11,
  test_shelly_runtime_error_redaction.py 5/5.
  Total new and selected existing: 184/184.
- Exact project Ruff 0.14.2 and maintainability gate PASS.
- Real external docker kill --signal SIGKILL after an
  instrumented SQLite BEFORE INSERT trigger reached L3,
  but before the transaction committed, returned
  Docker exit 137. Reopened synthetic SQLite: 0 rows,
  quick_check=ok, saved model pro3em and PHOTO marker intact.
- A separate real external Docker SIGKILL after a completed
  add_phases() transaction returned exit 137. Reopened
  synthetic SQLite: exactly L1/L2/L3, one shared timestamp,
  quick_check=ok, pro3em/PHOTO intact.
- Both QA Docker containers were deleted, temporary SQLite
  directories automatically removed. No public Telegram
  message, real Shelly RPC, or production restart occurred.

## Release acceptance still required

- Integrate with Issue #340's single lifetime writer owner,
  common host/container transaction lock, updater pending
  marker and service supervision. These QA sources are NOT
  installed on the currently signed running bot.
- Integrate Issue #422's newest whole-history WAL backup,
  PHOTO/send-intent parity, signed OCI/source/receipt pair
  and fail-closed recovery before any update to production.
- Test real legitimate partial sensor reads, stale phase
  readiness and low-but-real voltage without false zeros.
- Run exact-candidate signed image and reproducible
  crash/rollback tests on isolated Docker/systemd. Require
  all Python 3.11/3.12/3.13 CI results and exact-head
  Owner Approval before any release.
