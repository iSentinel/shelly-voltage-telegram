# Issue #340 — reviewed migration sequence from ungated old OCI

**Design and QA checkpoint only. Production release decision: NO_GO.**

This plan is based on the actual negative LXC114 experiment in Draft
PR #374: the older application initialized six synthetic SQLite
tables while the host held maintenance G EX. Existing ungated
Docker/systemd writers cannot be controlled retroactively by a
new file lock. Do not deploy on the strength of green CI.

## Mandatory P0-P8 owner transition

| Phase | Required proof | If proof is absent |
| --- | --- | --- |
| P0: preflight | Candidate OCI and source signature; whole CURRENT SQLite including WAL and metadata; **two independently restored backups on physically separate media**; explicit Owner Approval | NO_GO |
| P1: freeze old starts | Disable and verify autonomous Docker restart for old image, existing systemd boot/reload, manual Compose, old installer/updater/recover/rollback, watchdog and emergency starts using controls **outside** G | NO_GO: old image ignores G |
| P2: admission barrier | Trusted stable root-owned G/B/W/A inodes outside source/rollback tree; hold G EX by one coordinator while old start paths remain frozen | Keep old starts frozen |
| P3: quiesce old | Stop **all** old writers and verify every PID and SQLite transaction has exited. Confirm no daemon restart and no additional old container appears | NO_GO, do not treat B EX as proof old writer is gone |
| P4: maintenance locks | Under G EX, obtain B EX only after P3, then obtain updater A EX without nested service-prestart deadlocks | Fail closed |
| P5: current backups + signed stage | Verify two physically independent restores of the consistent CURRENT SQLite including WAL; stage a signed G-aware image and all cooperating service/updater/install/rollback/restore actors | No activation until both restorations pass |
| P6: one-owner handoff | Finish recovery and durable metadata; release A EX then B EX, and finally release G EX **only after every old start is blocked and every allowed new start is G-aware**. New image acquires G SH -> B SH -> W EX before touching SQLite | No automatic fallback to ungated older image |
| P7: live acceptance | Signed runtime digest, ready=200, sustained samples, SQLite quick_check, one lifetime B/W writer, real Telegram long-poll and worker shutdown, no pending update | Re-enter guarded maintenance, not uncontrolled restart |
| P8: closeout | Reviewed evidence, independent power-loss acceptance, explicit Owner Approval, separate pinned Telegram PHOTO approval | NO_GO |

## Deadlock and restart caveats

The production Compose unit currently runs the updater recovery in
ExecStartPre, and detached Compose in ExecStart/ExecReload.
The updater takes only the old A lock. If the coordinator retains
A EX while starting systemd and the service prestart reacquires
A EX, the system deadlocks. Recovery must have one reviewed owner
and no nested A acquisition. A maintenance B EX also cannot be
acquired while a writer still owns lifetime B SH.

A transient systemd flock G SH around a detached Docker start is
**not** a lifetime fence. The new application itself must obtain
G SH -> B SH -> W EX, release G SH only after admission and retain
B/W through all Telegram and HTTP worker completion. Docker may
autonomously restart regardless of host advisory locks; a new gated
app can refuse SQLite access but the old ungated image cannot.

Unattended rollback to the previous ungated image is unsafe.
Rollback must target a separately verified gated image, or leave
service stopped under explicit maintenance until a manually
approved independent recovery restores safe admission.

## Physical independence and current status

LXC110, LXC114, existing compressed SQLite archives, source and
updater state are on one physical Proxmox NVMe. Two files or two
guest directories on this hardware are **not** independent
failure domains. No pair of independently restorable whole
CURRENT signed source + WAL-complete SQLite backups exists yet.

Read-only source audit Draft PR #372 identified nine unresolved
owner paths. Actual QA real-Docker, real-app and transient
systemd tests in Draft PRs #363-#374 establish properties of
cooperating candidates only. They are not production migration
acceptance. The Pro 3EM/3CT63 synthetic parser has its own
hardware-onboarding plan in Draft PR #373.

**As of this document: P0 and P1 FAIL; P2-P8 NOT ATTEMPTED
IN PRODUCTION.** No production source, Docker, SQLite, restart
policy, meter configuration or Telegram may be changed without
these gates and explicit Owner Approval.
