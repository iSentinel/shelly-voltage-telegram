# Issue #340 — shutdown quiescence before B/W writer lease release

**QA-only stacked candidate. Production deployment NO-GO.**

Base: draft PR #363 (opt-in `writer_fence.py` plus actual
`Monitor.run()` and `Storage.connect()` fencing).
This draft only changes the real app's shutdown lifecycle and
adds self-contained regression/real Docker QA. It is **not**
a production Docker/Compose/systemd/updater change.

## Problem and reproducible negative evidence

The current application's Telegram command thread can persist
the update offset to SQLite. The local HTTP `/metrics` endpoint
also reads SQLite from request handler threads. Before this patch,
`Monitor.run()` launched `telegram-commands` with
`daemon=True`, did not hold a reference to this thread,
and never joined it. `start_health_server()` launched a
separate listener without keeping its server handle on
the monitor, so HTTP handler drainage was unproven.

A real isolated LXC114 synthetic SQLite WAL experiment
confirmed that when B/W scope ends while a background
SQLite transaction remains open, the maintenance B EX
can be acquired **before that earlier transaction commits**.
A positive control joined the worker before releasing the
locks and did not permit B EX overlap.

## Source-level shutdown contract

When `Monitor.run()` leaves its loop by SIGTERM/SIGINT or
an exception, the program:

1. Signals the global `STOP` event first.
2. Calls `health_server.shutdown()` to stop accepting HTTP
   traffic, then `server_close()` to wait for request handlers.
   The server is configured with `daemon_threads=False` and
   `block_on_close=True`.
3. Joins its owned non-daemon `telegram-commands` thread
   without a timeout. Telegram long-poll uses an application
   HTTP timeout, but no arbitrary lock lease deadline is
   applied: a slow writer cannot be ignored.
4. Returns from `Monitor.run()` **only after** these workers
   have completed. The surrounding opt-in B/W fence from
   draft PR #363 then releases its file descriptors.

The server returned from `start_health_server()` is now
stored as `self.health_server`, and early initialization
failures also take the cleanup path. For an *exception during
shutdown itself* in explicitly required fence mode, the
candidate logs a generic fatal message and uses
`os._exit(70)` as **fail-stop**: Linux then terminates
all process threads and releases B/W together instead of
returning to the outer `with writer_fence` while a writer
may still be active. In legacy mode (flag absent/off), no
forced exit is introduced.

**Implication:** if the Telegram network request never
completes or an HTTP request handler hangs, lock release
does not occur on a fixed timeout; Docker may eventually
deliver SIGKILL. This is intentional safety behaviour
but requires real production timeout/availability review.

## Isolated tests and evidence

Everything executes on exact `shelly-docker-reboot-qa`
LXC114 with only loopback. The original signed QA Docker
writer is stopped with restart=no and its two unit owners
are disabled. This LXC shares the **same physical Proxmox
host** as production and cannot demonstrate independent
physical power-loss safety.

- **46/46** strict unit tests for optional source fence,
  actual extracted `main()` and `Storage.connect()`,
  plus the real extracted `Monitor.run()`, PASS locally.
- **12/12** unit tests for the default-inert disposable
  real-app SIGTERM QA harness, PASS locally.
- **58/58** combined unit tests PASS as nonroot UID65534
  on scratch-only copied source. No root privileges are
  required in GitHub Actions unit tests.
- Ruff `E,F,I,UP,SIM` PASS on new Python modules/tests,
  all new functions <=40 lines and source lines <=79 columns.
- A real synthetic background WAL transaction inside a
  thread was intentionally kept open until after the
  shutdown path had stopped HTTP acceptance. The actual
  extracted `Monitor.run()` **kept both B SH and W EX
  held** until that worker was joined and the SQLite
  commit finished, then freed both.
- A separate *actual* loopback `ThreadingHTTPServer`
  experiment showed that `shutdown()` plus
  `server_close()` with non-daemon request handlers
  waited for an already-active HTTP request.
- The candidate's actual current `app.py` was run
  with `SHELLY_WRITER_FENCE=required` in an exact
  owner-labelled, nonroot UID10001 networkless
  read-only disposable Docker container using a
  **synthetic** SQLite directory, no Telegram credentials,
  and only the fixed cached QA OCI image. Host B EX and
  W EX probes failed during real app runtime.
  A real Docker SIGTERM ended the app cleanly
  **exit 0**. Afterwards both host B EX and W EX were
  available; invented SQLite `quick_check=ok`
  with six initialized tables. No disposable
  containers or temp dirs remained.

### Commands

Default inert anywhere:

```bash
python3 scripts/disposable-real-app-sigterm-qa.py --help
python3 scripts/disposable-real-app-sigterm-qa.py
python3 -W error::ResourceWarning -m unittest discover \
  -s tests -p 'test_writer*.py' -v
python3 -W error::ResourceWarning -m unittest discover \
  -s tests -p 'test_disposable_real_app_sigterm_qa.py' -v
```

Explicit isolated LXC114 only after checking the
original QA writer remains STOPPED with restart=no,
original QA units DISABLED and no external interface:

```bash
python3 scripts/disposable-real-app-sigterm-qa.py \
  --run-disposable \
  --confirm OFFLINE_REAL_APP_SHUTDOWN_SIGTERM_QA_ONLY
```

## Remaining NO-GO release blockers

- **No live Telegram command long-poll** was used in the
  Docker SIGTERM integration experiment. AST/mocked
  synthetic real-WAL worker tests prove join ordering,
  but actual Telegram API, callback handling, long-poll
  and in-flight real status-post handling remain untested.
- No verified whole-CURRENT SQLite+WAL backup A/B on
  independently restorable physical storage, and no
  physical host power-loss simulation across independent
  failure domains.
- No global single-writer control across Docker autorestart,
  systemd ExecStartPre/Start/Reload, manual Compose,
  updater/recover, installer/rollback or emergency restore.
  The installed emergency helper can re-enable
  `restart=unless-stopped`. Stable root-owned B/W lock
  inode identity across those paths remains unimplemented.
- The installed production source version differs from
  the running immutable signed OCI runtime. An exact signed
  source/current database restore and independent security
  review are required.
- **Owner Approval is required** for any change to the
  installed app, Compose, SQL database, restart policy or
  Telegram pinned message. The public TEXT-to-PHOTO
  transition remains independently NO-GO.

No production runtime/container/network/SQLite/Telegram
was restarted, accessed for writes or modified in QA.

Related issues: #340; draft PRs #336, #358–#363.
