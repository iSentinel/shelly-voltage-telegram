# Issue422: stopped-owner newest SQLite/WAL rescue

Status: source-only isolated QA, not deployed, not a complete rollback.

A new source-only scripts/fenced-newest-rescue.py operation requires:
- Root-owned shared SVT_WRITER_FENCE_DIR and valid pending update marker.
- The same transaction-gate inode used by the real app Storage.
- Successful nonblocking exclusive acquisition of the lifetime
  owner lock, proving the process cannot still own the writer lease.
- A private root-owned 0700 output directory and nonexistent output
  path; no existing rescue archive can be overwritten.
- SQLite backup API source snapshot, including committed WAL pages.
- Restored SQLite quick_check, real L1/L2/L3 data, pro3em model and
  Telegram PHOTO identity, full logical SQLite schema/row digest
  equal to the source, and durable file and directory fsync.

LXC114 actual synthetic QA:
1. Real nonroot Docker Storage creates saved pro3em/PHOTO and
   commits two complete L1/L2/L3 cycles while a second read-only
   Docker process keeps an older WAL snapshot open.
2. Root rescue without pending => blocked before copying.
3. Root publishes pending atomically under shared transaction gate.
4. Writer still owns lifetime lock => rescue refused BUSY.
5. The writer exits and relinquishes lease. Reader still holds
   WAL sidecar, which exists and is nonempty.
6. Root copies newest committed DB including WAL; quick_check OK,
   6 measurements (2 per phase), saved model and PHOTO retained,
   source/backup complete logical SQL SHA256 digest identical.
7. Attempting overwrite of the existing rescue is refused.
8. Isolated Docker processes, fixture and marker cleaned.
No real Shelly, Telegram, network or production SQLite were accessed.

Remaining: connect this guarded rescue to the actual signed updater
and restore, validate exact old/new OCI/source pair and preserve
newest rescue across SIGKILL. Do not activate updater or roll back
the current LXC110 storage from an older archive.
