# Issue340: root-owned shared fence directory acceptance

Status: source-only QA. Never deployed to LXC110.

The previous shared transaction marker lived beside the SQLite database.
Because the bot UID 10001 owns that directory, file mode 0644 alone did
not stop the bot from unlinking a root-published update marker. That
security weakness blocks OTA and unattended rollback.

The revised app requires the environment variable SVT_WRITER_FENCE_DIR
at the actual monitor entrypoint. It rejects relative, linked,
non-root-owned or group/other-writable ancestors and the database
directory itself. The root-protected fence directory is bind-mounted
read-write into the container at a distinct path; it contains exactly
two regular UID 10001 mode-0600 locks and one ephemeral root-created
mode-0644 pending update marker. UID 10001 can flock its locks but
cannot unlink the root-owned marker from the root-owned directory.

The root-only idempotent prepare-writer-fence.py CLI creates/validates
the directory and both lock files. The root update marker CLI and the
nonroot app must see the same underlying directory and lock inode.

Isolated LXC114 verified:
- Root directory mode 0755, lock inode owner UID 10001/mode 0600.
- Repeated root prepare commands PASS and never touch a pending marker.
- Real networkless Docker UID 10001 ran Storage.initialize, saved
  pro3em/PHOTO metadata and atomically committed L1/L2/L3.
- Root updater published pending under the same lock inode.
- Real container unlink attempt raised PermissionError.
- Second real Storage.add_phases was blocked by SVT_UPDATE_PENDING.
- Isolated SQLite quick_check OK, 3 original phase rows, PHOTO intact.
- Exact root clear allowed new writer, 6 rows total (2 per phase).
- No real meter, Telegram credentials, live database or network mount.

Remaining gates: candidate Compose must mount this directory, remove
independent Docker restart ownership, and be supervised by systemd;
actual updater, newest-history rescue and signed rollback must use
this same root-owned gate. No source/OCI promotion or live reboot.
