# Issue #340 — isolated Python for inherited-G owner preflight

**QA-only stacked Draft, production NO_GO.**

## Finding and bounded mitigation

The trusted command-path hardening from #386/#388 checks which Docker
and systemd binaries are executed, but the original frozen-owner
preflight process itself was invoked via an ordinary `python3`.
A caller-controlled `PYTHONPATH` or `PYTHONHOME` could change what
its standard-library imports mean before any trusted owner check.

Actual Proxmox LXC114 synthetic proof:
- Unisolated Python with private fake `shutil.py` wrote a local
  poison marker during inert `--help`: import hijack reproduced.
- Running the same candidate with `python3 -I` did not import the
  fake module and created no poison marker.
- Both LXC110 and LXC114 have root-owned `/usr/bin/python3` resolving
  to `/usr/bin/python3.13`. This observation does not replace a
  future trusted interpreter inode/parent verification.

The candidate invokes only the inherited-G owner preflight through
`/usr/bin/python3 -I` in `scripts/apply-update.sh` and
`scripts/rollback.sh`. It does not change the default app Python
runtime, install services, access current SQLite, or approve rollback.

## Isolated acceptance

In private loopback-only LXC114 scratch, actual G EX, mocked
root-owned Docker/systemd, fake poisoned module and bogus PYTHONHOME:
- **5/5 tests PASS root** (1 expected nonroot-only skip)
- **5/5 tests PASS UID65534/nobody** (2 expected root-only skips)
- Both Bash parser syntax checks PASS
- Exact Ruff 0.14.2 format/E/F/I/UP/SIM and <=79 width PASS
- No synthetic poison marker was imported by candidate entrypoints
- No real Docker, systemd, Telegram, Shelly or production SQLite call

## Not solved / release gates

This protects only the **owner preflight interpreter invocation**.
Do not generalize it to arbitrary Python calls in install/update,
old rollback, shell initialization, `BASH_ENV`, executable PATH
resolution before Bash runs, or compromised root-owned interpreters.
Full environment sanitization, global old-owner G/B/W/A fence,
approved pending boot fallback, stop-writer CURRENT signed-source and
WAL two-copy backup+restore, real Telegram long-poll termination,
physical power-loss acceptance, independent security review and
explicit Owner Approval recognizing a single-NVMe total-loss risk
remain NO_GO. Public pinned-photo migration is independently NO_GO.

This change is stacked on #388 (which includes code from sibling
#386); final branch ancestry must be reconciled before merge.
