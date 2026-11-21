# Stop-first cutover journal (QA component)

The published 0.5.26-hw application and immutable tag are unchanged.
The legacy running 0.5.25-hw writer does not honor the new writer fence.
A new installer needs to persist the intent before every external action
and independently observe the result before marking it complete.

The journal library in scripts/cutover_journal.py records ordered
intent/observation pairs bound to one version, Git SHA, source SHA256,
OCI digest and transaction token. It uses a private exclusive lock,
atomic replacement and file/directory fsync. Interrupted intents are
reported RECONCILE_REQUIRED, never replayed automatically. A digest of
an evidence record is a reference, not proof that its contents are true.
The journal grants no authorization and does not execute shell, Docker,
systemd, database, Telegram or release actions.

Next integration work: implement actual independently verified stage
adapters, including durable inhibition of *all* legacy restart paths,
quiescent rescue, strict signed pair receipt and supervisor activation.
Do not enable a production execution backend until those adapters and
interruption/reboot behavior pass isolated end-to-end tests. Preserve
the latest database; never restore older SQLite as automatic recovery.
The already approved e704730 application is distinct from this new
unpublished tooling; no new release/tag or deployment is authorized by
journal completion.
