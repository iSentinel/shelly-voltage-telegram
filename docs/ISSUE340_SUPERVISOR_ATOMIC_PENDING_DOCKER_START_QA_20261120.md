# Issue 340: hold update gate through Docker supervisor start

Date: 2026-11-20 Europe/Kyiv
Scope: source-only, LXC114 isolated QA. Production unchanged.

## Race being closed

Previously supervised-container.py called pending(database) under
the shared .svt-transaction.lock, released the lock, and only
then called docker start. A concurrent updater could publish
.svt-update-pending in that gap. A correctly fenced writer
would subsequently refuse SQLite access, but the supervisor
could still start a container while an update was already
pending. The authoritative boot owner should not perform this
operation under such an interleaving.

## Implementation

- _pending_under_gate() checks the exact root-owned protected
  marker while its caller already owns the transaction lock.
- Existing pending() keeps its public behavior and acquires
  the transaction lock before inspecting any marker.
- start_if_clear() holds the SAME transaction lock continuously
  across the pending-marker check and the bounded docker start
  invocation, returning false without Docker when pending.
- An unsafe marker or failed docker start still blocks
  without claiming supervised startup.
- supervise() uses start_if_clear() and prints SKIP_PENDING when
  the marker was published first.

No attempt is made to clear a pending marker, replace a DB,
restart an autonomous writer, or perform a production cutover.
The host and container must still share the same lock inode,
and the writer has to acquire writer_lease to start any DB work.

## QA evidence

- LXC114 80/80 targeted supervisor, unit boot policy, stage
  and paired cutover tests PASS; Ruff0.14.2 and quality PASS.
- A real two-thread test shows that a competing updater's
  update_gate cannot acquire the transaction lock while
  the supervisor holds it during docker start.
- Preexisting pending, dangling symlink marker, and failed
  docker start all fail closed without claiming an owner.
- Real disposable tests/qa_compose_supervisor_runtime.sh PASS:
  create-only restart=no, sole-owner startup, atomic L1/L2/L3,
  pending prevents restart and safe clear resumes phase writes.
- Root-owned paired receipt test PASS, including tampered source,
  OCI revision mismatch and unsafe receipt rejection.
- QA Docker idle with no .svt-update-pending after cleanup.

## Limitations

This source-only fix does not install the candidate on LXC110.
Live Docker still has restart=unless-stopped and the old systemd
oneshot updater/rollback hooks. Signed old+new source/OCI,
complete newest WAL/PHOTO continuity, true hard-process failure
acceptance, and immutable production release publication remain
unresolved production gates.
