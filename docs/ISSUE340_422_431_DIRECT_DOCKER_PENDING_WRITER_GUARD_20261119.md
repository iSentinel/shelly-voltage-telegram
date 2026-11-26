# Issues 340/422/431: direct Docker start during a pending update

Date: 2026-11-19, Europe/Kyiv
Status: isolated LXC114 QA, never a production change.

## Threat

The planned sole-owner systemd supervisor refuses to start another
writer while the root-owned update marker exists. However an operator
or a competing service can invoke Docker start directly and bypass
the supervisor's launch check. The application-level writer lease and
SQLite transaction gate must independently prevent such a container
from writing an obsolete or inconsistent database.

## Actual offline QA

Extended tests/qa_offline_image_switch.sh to call Docker start
DIRECTLY on the next disposable writer at both forward and reverse
image-switch boundaries, while the exact pending marker is active.

Both phases:
- use two distinct, locally built unsigned Docker image IDs;
- stop the previous QA writer under a published pending token;
- preserve an offline newest full SQLite/WAL rescue;
- verify the supervised start is SKIP_PENDING;
- attempt direct Docker start on the next stopped QA container;
- wait for the direct-start process to exit nonzero, requiring that
  it DOES NOT remain running;
- verify PRAGMA quick_check ok and the exact prior three-phase
  rows, PHOTO identity, Telegram uncertain send and cleanup queue;
- require correct-token pending release before subsequently
  launching the next image via the supervisor.

The separate --qa-crash-recovery mode first kills the disposable
update coordinator via SIGKILL at each forward/reverse boundary.
An intentionally wrong token cannot clear pending. A direct Docker
start bypass is still denied by the application-level writer fence.

Both actual LXC114 direct-start checks PASS; no unapproved SQLite
row was added. Subsequent controlled forward switch and reverse
continue all L1/L2/L3 rows with persistent PHOTO and Telegram intents.
The targeted static policy tests are 7/7 PASS and project Ruff0.14.2
quality gate PASS.

QA-only temporary images, containers, database and pending markers
were cleaned after the trial. Production LXC110 was not touched.

## Limitations

These two local images use the same app code; only their labels and
Docker IDs differ. There is still no verified new signed source+OCI
release or real cross-version schema migration. This confirms
application-level fail-closed behavior under a direct Docker
start, but it does not by itself eliminate competing systemd
restart owners or establish a safe production cutover.

## QA invocation

    bash tests/qa_offline_image_switch.sh --qa-crash-recovery

Only on offline LXC114 named shelly-docker-reboot-qa.
