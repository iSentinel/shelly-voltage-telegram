# 2026-11-18 — Combined YASNO + update-check source QA

## Scope

This is **source-only, owner-review-pending** QA for the independent draft
fixes in PR #418 (YASNO public source permission repair) and PR #419
(DynamicUser isolated update checker credentials).

Integrated QA branch: `qa/issue402-integrated-yasno-checker-20261118`.
The branch changes no monitoring logic, Telegram delivery, credentials, or
installed systemd units. Do not treat this QA as release authorization.

## Acceptance evidence

- The combined branch contains both changes and passed 22/22 targeted
  unittest cases on LXC110 (14 installer cases, 8 checker credential cases).
- `scripts/quality.sh` passed with Ruff 0.14.2: 245 Python files formatted,
  full lint and project maintainability checks PASS.
- `bash -n scripts/install.sh`, Python bytecode compilation for the checker,
  and `systemd-analyze verify` for its service unit passed.
- A **real PID1 systemd** transient DynamicUser service on LXC110 successfully
  loaded two public credentials from a source tree protected by `/root`.
  It loaded and parsed the public release marker and executed the copied checker with `--help` (exit 0), with
  `ProtectHome=yes`, `ProtectSystem=strict` and AF_UNIX-only address family.
- The repeatable, read-only, no-network test is provided as
  `scripts/qa-update-check-credentials-systemd.sh` and completed PASS.
  It verifies installed_version() against the release marker before --help.
  Its transient service is collected; it never starts the actual checker timer.
- Extended functional smoke verified on real systemd as an ephemeral
  RuntimeDirectory owned by the short-lived DynamicUser: an offline mocked
  release check wrote status.json with mode 0640, kept auto_install=false
  and did not write into the read-only credential directory. It also checked
  that the protected .env and secrets paths were not readable. The runtime
  directory is removed automatically after the test. No Telegram API,
  GitHub network request or production state was changed.
- The initial raw `systemd-run` argument containing `%d` did **not**
  expand in its transient command (expected CLI escaping distinction).
  The smoke uses the exact transient credential directory instead. This
  does not establish whether an installed production unit would succeed;
  after authorized installation, validate the literal service specifier
  and bounded real checker invocation separately.

## Operational safeguards

- Production signed image and running Docker writer were not restarted or
  modified during this QA. Monitoring remained healthy at the last read.
- No source release, PR merge, public Telegram send/edit, or PHOTO migration.
- Current update-check timer remains INACTIVE despite enabled unit state.
- Activation/install requires separate reviewed, approved change; do not
  weaken private project or secret-file permissions to make checker work.
- Physical Pro 3EM/3CT63 acceptance and subscriber-visible Telegram PHOTO
  acceptance are not established by these source-only tests.

## Follow-up gates

1. Review exact PR #418 and #419 changes together, including the helper test.
2. Confirm merged/release candidate against signed image and fresh,
   whole-history, stopped-writer rollback snapshots under owner approval.
3. After authorized install, run real checker service once and confirm
   status path/permissions and timer scheduling without auto-install.
4. Continue non-invasive Shelly, SQLite, Telegram and YASNO observations.
