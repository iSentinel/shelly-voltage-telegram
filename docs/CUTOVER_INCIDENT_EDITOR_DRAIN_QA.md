# Temporary Telegram editor retirement

The incident bridge runs independently of the native application scheduler.
A disabled timer alone does not stop its current service invocation, and
terminating the host-side Docker CLI is not proof that a Docker exec process
or its already submitted Telegram request has completed.

## Required ordering for a future reviewed cutover

1. Record the exact old container ID, immutable image, existing PHOTO
   binding and unresolved publication intent. Inventory all installed
   restart and editing owners, including updater and emergency services.
2. Stop the bridge timer and wait for the running service to finish
   naturally. Preserve the exact root-owned unit files and their hashes.
3. Retire both bridge units durably. Existing production unit files are
   under /etc/systemd/system, unlike the QA fixture; persistent replacement
   needs an explicitly reviewed transaction and recovery procedure.
4. Inhibit legacy Docker and service restarts, stop the old container and
   confirm its immutable identity and absence of running exec processes.
5. Observe the selected editor drain before rescue capture and native
   supervisor activation. An unknown remote Telegram outcome remains
   unknown; do not clear the pending publication or repeat its send.
6. Do not re-enable the bridge concurrently with the replacement editor.
   If interrupted, reconcile the journal with actual runtime state before
   deciding whether any existing exact binding may be safely resumed.

The QA observer checks two persistent masks, inactive/dead unit state,
no queued jobs or service PIDs and a stopped, exact, no-restart container.
It does not perform those operations, certify all editors, reconcile
remote delivery, or grant deployment/rollback authorization.

The live QA fixture uses a dummy sleep service, network-none container and
the previously signed old image. It has no Telegram credentials or traffic.
