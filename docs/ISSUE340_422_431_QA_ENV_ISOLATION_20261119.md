# Issues 340/422/431: isolate QA fence env between unittest cases

Date: 2026-11-19. State: QA branch only.

The first GitHub Actions matrix for SIGKILL stage commit
969bbd51432984bd52d43aaa5834c703b997e397 failed. CI
run 44505036485 failed Python3.12 Unit tests and cancelled the
other matrix jobs. The full suite reported 2279 tests, 7 failures,
81 errors and 1 skipped. This result was NOT a release PASS.

Root cause identified from the first traceback: newly added
direct unit calls into the QA stage's resume_pending() set
SVT_WRITER_FENCE_DIR to /var/lib/svt-qa-check-fence in the
unittest runner process. The synthetic directory did not exist;
the un-restored env leaked into the next independent tests.
Their real Storage.connect() correctly rejected the nonexistent
protected fence. Subsequent legacy updater tests correctly refused
running with a supervisor fence configured.

This was a test isolation problem, not a valid proof that those
81 independent components had failed in production.

The QA policy TestCase now starts a patch.dict(os.environ)
environment snapshot in setUp and always restores it through
addCleanup, including failure paths. In addition, the source-only
QA stage now checks that its output directory is root-owned 0700
BEFORE stopping any writer on a pending-resume request, using a
shared require_private_output_dir helper. Unit mocks replace only
that filesystem permission predicate when running as UID10001;
the real protected path check is not relaxed.

LXC114 isolated Docker verification (network none, UID10001,
read-only rootfs, Python3.13 runtime dependencies) ran the stage
policy tests followed in the SAME interpreter by the previously
failing status cleanup, writer-marker, legacy updater/rollback,
voltage editor, warning, and Telegram text identity tests:
85/85 PASS, zero ResourceWarning. Ruff0.14.2 and project
quality/maintainability checks PASS.

This local test is NOT a substitute for successful exact-head
GitHub CI on 3.11/3.12/3.13. No production image, SQLite, service,
Telegram message, updater timer or restart policy was changed.
