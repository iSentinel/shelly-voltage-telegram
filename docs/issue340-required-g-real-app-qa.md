# Issue #340 — opt-in real app `required-g` admission before B/W

**QA-only stacked candidate. No production deployment, merge or Owner Approval.**

This PR is stacked on draft #367 (real Docker synthetic G/B/W/A
handoff) and earlier drafts #366/#365/#364/#363. It adds explicit
`required-g` mode to the real `writer_fence.py` used by `app.py`.

## Safety contracts

- `SHELLY_WRITER_FENCE` remains **off** by default, with no file
  creation and no changes to the running installed production image.
  Existing `required` still acquires **B SH -> W EX** without G,
  preserving the previous behavior for reviewer comparison.
- **Only** new explicit `required-g` first opens root-managed
  `G` with `O_NOFOLLOW`, verifies owner UID0, group matching
  the nonroot writer, mode0640 and link count 1, then acquires
  **G SH -> B SH -> W EX** with nonblocking `flock`.
- It validates the G/B/W directory/file inodes, releases
  **G SH once B SH and W EX are acquired**, and retains B/W
  throughout `Monitor.run()` and full HTTP/Telegram worker
  shutdown. The G file descriptor remains open and its
  pathname/metadata is checked on each SQLite connection
  even after its brief lock is released.
- If G EX is held during maintenance, `required-g` refuses
  startup **before any SQLite initialization**. Unexpected
  symlink, hardlink, replacement, owner/group/mode mismatch,
  repeated/downgraded mode or B/W conflict fails closed.
- The guarded shutdown `os._exit(70)` fallback applies to
  either `required` or `required-g`. This behavior is
  QA-only until the entire updater/owner state machine and
  appropriate restart policy are accepted.

## Tests

- 15 new strict `test_writer_admission_g.py` tests: G EX
  denial, B/W admission, release G after acquiring B/W,
  legacy `required` compatibility when G is absent, SQL
  preflight, mode downgrade, inode replacement, unsafe mode,
  symlink/hardlink/alias, G release on W conflict, and inert
  `off` behavior.
- Combined cumulative QA workspace: **120/120 tests PASS**
  as root and as UID65534; Ruff E/F/I/UP/SIM, PEP8,
  line width <=79, new functions <=40 lines PASS.

### Actual disposable real application Docker QA

On the exact `shelly-docker-reboot-qa` LXC114 with only
loopback NIC, original signed writer STOPPED restart=no and
original QA systemd units DISABLED:

1. Copied the **current candidate `app.py` and
   `writer_fence.py`**, plus VERSION from exact cached
   0.5.21-hw QA image, into an invented temporary code
   mount. The real app uses invented SQLite and
   `SHELLY_HOST=127.0.0.1`, `SHELLY_WRITER_FENCE=required-g`,
   no Telegram credentials or network.
2. Host held synthetic G EX while launching the **actual
   app `run`** command in offline read-only nonroot
   UID10001 Docker. App returned a nonzero status with
   `owner_fence_already_held`, and **SQLite file absent**.
3. After host released G EX, launched the real app in a
   separate owner-labelled disposable Docker container.
   Host **G EX could be acquired** while writer was live
   holding B SH/W EX; host B EX and W EX were refused.
4. Real Docker SIGTERM ended the app with **exit code 0**;
   host G/B/W locks were all available after exit and
   synthetic SQLite `PRAGMA quick_check=ok`. Exact
   disposable object was removed; no production changes.

This is a **direct real application + real Docker** lock
proof but **Telegram is disabled** and the Shelly meter is
only a dummy loopback destination. It cannot authorize
production Telegram status changes or a full signed release.

## Release NO-GO

The candidate introduces a **new opt-in writer admission
protocol**, not a completed maintenance implementation.
Production currently runs immutable signed 0.5.21-hw
and installed host source is older; neither has the
new G mode deployed or enabled.

Real updater `apply-update.sh` and `--recover` still
own the old A lock, `start-compose.sh` launches detached
Compose, `install.sh` may set Docker restart
`unless-stopped`, and rollback/emergency workflows can
mutate source and SQLite. **All actual owners** must
use a reviewed noncircular G EX -> stop all writers ->
B EX -> A EX protocol, preserving stable root-owned
G/B/W/A inodes across normal operations and failures.

Outstanding acceptance also includes actual Telegram
long-poll/handler shutdown, whole current signed source
+ WAL-complete SQLite A/B independently restorable
on physically separate storage, physical power-loss,
independent review, correct deployment rollback/recovery,
and explicit Owner Approval. LXC110 and LXC114 share a
single physical Proxmox host. Public pinned Telegram
PHOTO remains separately NO-GO.

Do not merge/deploy/restart production based solely on
green QA/CI. No production database, signed image,
container, restart policy, Telegram, or sensor network
was changed in these tests.
