# Combined QA source-level acceptance — 2026-11-08

This separate worktree combines the **source changes** from the
following Draft PRs for independent regression:

- #202 — community schedule adapter, stale-safe multilingual widget,
  fallback caption and pinned QuickJS workflow.
- #203 — Kyiv-local Unix-midnight date keys in the DTEK cache adapter.
- #204 — exact Shelly model-ID verification in hardware preflight.

All three branches were created from QA base `b7016e8`. Files were
copied explicitly to a clean worktree without changing the base branch.
The integration has not merged the PRs and does not certify official
DTEK provenance, physical Pro 3EM / 3CT63 operation, or widget display
on installed Telegram clients.

## QA acceptance

```bash
scripts/quality.sh
PYTHONPATH=.:tests python3 -m unittest discover -s tests -p 'test_*.py' -q
python3 scripts/shelly-hardware-preflight.py --model pro3em --dry-run --json
```

Observed: 1000/1000 Python tests PASS, Ruff and quality gate PASS;
widget HTML 8439 raw bytes / 3205 zlib-9 bytes.

Remaining release blockers: real Telegram client support, recipient
visibility, official DTEK source attestation, physical multi-meter
testing, and explicit approval. This integration branch is **not**
authorized for production deployment.
