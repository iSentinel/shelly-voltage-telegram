# Issue #402 — backup fault-injection QA sandbox

This note documents a **test environment requirement**, not an exception
to production backup security.

## Finding on 2026-11-17

The `test_failed_sidecar_publish_cleans_archive` regression test creates
a private fake `ln` executable under `tempfile.TemporaryDirectory()`.
It prepends this directory to `PATH` to make the checksum-sidecar
publication fail with exit 23.

A locked-down disposable Docker container with a **noexec** `/tmp`
does not execute that fake helper. Shell lookup may instead resolve
`/usr/bin/ln`, and the backup succeeds because the simulated fault
never happened. The original assertion then misleadingly blamed
`backup.sh` instead of identifying a missing test precondition.

The regression now checks `shutil.which("ln", path=env["PATH"])` and
requires the exact fake executable **before invoking the backup**.
On noexec, the test explicitly reports:

`Fault injection needs an executable temporary directory`

This is a failed QA setup, **not** a verified backup defect or a test
PASS.

## Disposable test invocation

Use the application's previously verified immutable Docker image,
with the candidate source mounted **read-only**. Explicitly permit
execution only on the container's ephemeral, private `/tmp`.
Retain `--network none`, all capabilities dropped, no-new-privileges
and a read-only root filesystem.

```bash
docker run --rm \
  --network none --read-only --cap-drop ALL \
  --user 65534:65534 --security-opt no-new-privileges \
  --memory 512m --pids-limit 80 \
  --tmpfs /tmp:rw,exec,nosuid,nodev,size=256m,mode=1777 \
  --mount type=bind,source="$QA_SOURCE",target=/qa,readonly \
  --workdir /qa --env PYTHONDONTWRITEBYTECODE=1 \
  --entrypoint python "$QA_IMAGE" \
  -m unittest discover -s /qa/tests \
  -p test_backup_archive_uniqueness.py -q
```

Run `--help` on the QA command's Python runner or the actual backup
tool when needed. Never apply `exec` to a production host temporary
mount solely to satisfy tests.

The executable mount is **QA-only**, newly created for each run,
holds synthetic data only, and is discarded on container exit.
No Telegram API calls or production SQLite writes occur in the tests.

## Acceptance

- In a noexec scratch filesystem the test must fail explicitly on the
  missing executable stub, before pretending to inject failure.
- In the isolated executable QA scratch filesystem, the entire
  backup-uniqueness suite must pass for UID root and UID nobody.
- The backup archive verifier, restore drill, predeploy parity guard
  and actual archived SQLite restore checks remain separate gates.
- Neither a passing local restore nor an ephemeral container on the
  same NVMe proves independent off-host disaster recovery.
