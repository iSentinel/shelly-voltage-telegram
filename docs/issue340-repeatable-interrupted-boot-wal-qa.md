# Issue #340 — repeatable interrupted-boot/WAL acceptance

**Status: QA-only / DRAFT / production NO_GO.**

This stage is stacked on Draft #385. It does not add an automatic
current-image fallback, authorize source installation, modify the running
Docker service, restore SQLite, or send Telegram messages.

## Purpose

Prove that repeated boot recovery probes never interpret an old local
archive as permission to erase newer committed SQLite history. A pending
update intentionally remains blocked until the global owner freeze,
signed current runtime checks and an owner-approved boot-continuity
policy exist.

## Isolated test matrix (Gateway, Proxmox LXC114)

Network is loopback-only. The candidate code is staged into a private
temporary directory and `docker`/`systemctl` are mocked, with actual
Linux G `flock` in the privileged case. No production paths are used.

- Two normal boots without pending marker: inert, no state created.
- Two interrupted boots with an old bogus gzip archive: both fail
  without changing WAL, current 30 rows or the pending marker.
- Malformed marker: still refuses and retains the marker.
- Symlinked pending marker: refuses before touching state.
- Real inherited G EX with mocked frozen owners: performs only
  read-only inspections and still returns NO_GO.
- Nonroot `--recover` attempt: denied before state inspection.
- Candidate Bash syntax: PASS.

**7 targeted tests as root PASS (1 expected nonroot-only skip).**
**7 targeted tests as UID65534 PASS (5 expected root-only skips).**
The synthetic SQLite connection remains in WAL mode while tests run;
WAL bytes, count of 30 and `PRAGMA quick_check=ok` are checked afterward.
Temporary QA state is cleaned.

A related security fix, **Draft #386**, is a sibling stacked branch on
#384, not an ancestor of this Draft. It validates trusted absolute
Docker/systemd executable paths before owner inspections. The
PATH-driven fake commands in this harness are deliberately synthetic:
they cannot prove production command-path authenticity. Integrate
and re-run #386's hardening before treating any inherited-G owner
preflight as trusted, and retain final release NO_GO in either case.

This acceptance is intentionally narrower than a real Proxmox
power-loss or production systemd reboot test. GitHub CI, release
signature and final current backup acceptance must be validated
separately before any deploy decision.

## Still blocked

Global old owner quiescence (Docker auto-restart, systemd/Compose,
installer, updater, rollback and emergency owners); stable root-owned
G/B/W/A admission; current signed-source and OCI provenance; two
current whole-WAL SQLite restorable backups within the accepted single
NVMe failure domain; long-poll shutdown; power-loss QA; independent
security review; and explicit Owner Approval acknowledging possible
loss of all local data after an NVMe failure.

**No script in this Draft is a production boot fallback.**
