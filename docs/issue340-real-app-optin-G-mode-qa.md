# Issue #340 — opt-in G admission in the real Shelly writer source

**Review-only candidate; not installed in production.**

This source-only stage is stacked on Draft PR #367, which tests
G/B/W/A as a synthetic real Docker process; #367 depends on
#366/#365/#364/#363. The actual signed production Shelly image
and updater are unchanged.

## Explicit new mode

`SHELLY_WRITER_FENCE=required-g` is an additional **opt-in**
mode alongside the existing unmodified `off` and `required`
modes from Draft PR #363. The default remains inert. No G,
B or W files are created/replaced by the writer.

The new mode requires root-owned `/run/shelly-owner/`
(directory mode0750, group the application effective GID)
and separate mode0640, root-owned regular files B, W, G,
group10001, nlink1. They must be pre-created and mounted
readonly from a stable host root-managed path; changing
their device/inode identity, ownership, mode or link count
is a fail-closed error.

1. Open G using `openat(O_NOFOLLOW|O_RDONLY|O_CLOEXEC)`
   relative to the verified owner directory and acquire G SH,
   nonblocking. If host maintenance holds G EX, stop before
   acquiring B/W or opening SQLite.
2. Open and acquire B SH then W EX, nonblocking. Reject aliases,
   unsafe metadata and missing files.
3. **Release G SH only when both B SH and W EX were obtained.**
   Keep an open G fd for continuous identity verification before
   new SQLite connections. Hold B SH and W EX through all
   application writer threads and shutdown per Draft PR #364.
4. On exit or any failure to acquire the locks, close the exact
   file descriptors and release the locks. Strict fail-stop
   during shutdown is retained for both `required` and
   `required-g`.

No installer, updater, rollback or emergency service gains a
G integration merely by merging this code; production remains
NO-GO. The trusted UID is 0 by default; unit tests patch
ownership expectation only inside temporary fixtures to
support unprivileged GitHub CI.

## Actual offline LXC114 observations

- **15/15** new root-only synthetic inode unit tests PASS:
  G EX denial, G release after B/W, missing G, symlink,
  hardlink, wrong mode, inode replacement before SQLite,
  accidental downgrade, W conflict cleanup and legacy mode
  compatibility.
- **120/120 cumulative strict unit tests PASS** locally;
  all new functions <=40 lines, all Python source lines <=79
  chars, Ruff E/F/I/UP/SIM PASS.
- Actual **candidate real `app.py` v0.5.21-hw** ran as
  UID10001 inside an isolated **network=none, restart=no**
  Docker container with a private synthetic SQLite database
  and no Telegram credentials. While host held G EX, the
  candidate failed before SQLite creation with
  `owner_fence_already_held`. With G free it acquired
  G SH -> B SH -> W EX and released G SH, permitting a
  host G EX even while B/W stayed held. On real SIGTERM,
  app exited **code 0**, locks released, synthetic SQLite
  `quick_check=ok`.
- LXC114 has loopback only; original signed QA writer
  stopped restart=no, original service/timer disabled.
  No test residue or production changes.

## Still outstanding

The **actual maintenance code** must implement G EX
before stopping the incumbent, then B EX and A EX,
with poststop WAL quiescence and full source/backup guards
across all Docker/systemd, updater/recover, installer,
rollback, manual Compose and emergency restore paths.
The real Telegram long-poll shutdown and HTTP handler
drain require production-like acceptance. Current signed
runtime v0.5.21-hw differs from installed host source
v0.5.15-hw. Whole CURRENT signed source+SQLite WAL A/B
must be independently restored across separate physical
failure domains, followed by host reboot/power-loss
acceptance and independent security review.
**Explicit Owner Approval** is mandatory before production
changes. Public pinned Telegram PHOTO remains NO-GO.

Do not enable `required-g`, add host mounts, change Docker
restart policies, install code or alter production SQLite
until every release gate is met.
