# Issues 340/422/431: paired receipt guard for candidate supervisor

Date: 2026-11-19, Europe/Kyiv
State: source-only QA; candidate unit is not installed.

## Gap

The original deployed.json receipt binds only release version and OCI
digest. A future sole-owner supervised release also needs exact source
revision and original signed source bytes. The old v0.5.25-hw runtime
receipt cannot satisfy the new fields. Boolean proof fields by
themselves are NOT cryptographic verification; they are meaningful
only after a separately proven and root-controlled release transaction.

## Candidate changes

- Candidate systemd blueprint, deliberately stored as .unit and never
  activated, requires --require-pair-receipt and --source-archive-dir.
- Candidate supervisor requires matching source RELEASE_VERSION and
  deployed version, immutable sha256 OCI image, Docker config revision
  label matching the exact receipt Git SHA, and a root-controlled
  receipt with mode0600, bounded size and a single file link.
- Receipt fields source_commit (40 lower-case hex), source_sha256
  (64 lower-case hex), pair_status and three exact proof flags are
  mandatory in paired candidate mode. An older digest-only record
  is refused without activating the container.
- The original source tarball must remain in the private root-owned
  0700 directory, under its exact versioned filename. It must be
  regular root-owned mode0600, single-link, bounded (512 MiB).
  The source tar SHA256 is recomputed and must match the receipt.
- Receipt ancestor directories must all be root-controlled and not
  group/world writable; parent symlinks, missing/foreign labels,
  missing archive or wrong digest lead to refusal.
- Docker must remain restart:no, and a foreign already-running
  writer must not be silently adopted. QA-only mode remains isolated.

The existing production service has its own Docker restart owner and
legacy database recovery hook; neither is changed by this candidate.

## Real LXC114 tests

- QA-only root-owned fixture, using the actual version file, receipt
  and synthetic source bytes: positive private receipt SHA verification
  PASS, tampered archive refused, wrong OCI revision refused, integer
  proof flag refused, insecure permissions refused, symlinked receipt
  or archive refused and missing source SHA refused. All QA files
  removed.
- Real offline Docker Compose create-only / sole supervisor test:
  starts three-phase synthetic writer, pending-marker blocks startup,
  controlled marker clear permits next writer; counts 3 to 102.
- Networkless read-only Docker unit regression: 242/242 tests PASS
  with UID10001 and Python3.13, including signed source/OCI pair,
  updater/rollback, SQLite fences, pending and Pro3EM phases.
- Ruff0.14.2, project quality and maintainability checks PASS.
- Last production read-only check at 13:11 Kyiv:
  signed v0.5.25-hw, healthy, ready200, restart0, pending absent.
- No signed new source/OCI release, no actual production cutover,
  no direct Telegram sends, and no production SQLite changes.

## Not yet authorized

These receipt checks prove only continuity of root-controlled
evidence. A root operator could write identical text flags without
ever verifying Cosign; therefore the candidate is NOT ready for
release. The next stage is an atomic receipt creator that performs
the real signed source/OCI exact-commit preflight before writing
the root-controlled record, and then an isolated full image-change
rehearsal with newest WAL/PHOTO rescue, SIGKILL and guest reboot.

Do not replace the production deployed.json with synthetic data,
run the legacy --recover hook, install the candidate .unit, or
publish a new image or Git tag. Owner Approval does not bypass
these technical release barriers.

## Safe QA commands

    bash tests/qa_supervisor_paired_receipt_runtime.sh --help
    bash tests/qa_supervisor_paired_receipt_runtime.sh
    python3 -m unittest -q test_supervised_container_policy

The live command is only allowed in isolated root LXC114 with
hostname shelly-docker-reboot-qa. It is not a production procedure.
