# Issues 340/422/431: two distinct Docker image IDs across two real QA reboots

Date: 2026-11-19, Europe/Kyiv
Status: source-only QA evidence from disconnected Proxmox LXC114.
Production LXC110 was neither rebooted nor altered.

## Added repeatable three-step offline rehearsal

The existing tests/qa_offline_image_switch.sh gains explicit
--qa-reboot-prepare, --qa-reboot-forward, --qa-reboot-reverse and
--qa-reboot-cleanup modes, in addition to its previously tested
single-session and coordinator SIGKILL modes.

The new stages are root-only and restricted to the exact isolated
hostname shelly-docker-reboot-qa and fixed named QA directories,
Docker containers and image tags. No production paths are used.

Preparation:
- Refuse preexisting disposable fixture paths, containers and tags.
- Build two different offline Docker image IDs from the same local
  unsigned runtime, retaining immutable image IDs in a private file.
- Seed a real SQLite database with three-phase records, PHOTO
  identity, uncertain Telegram send and a durable cleanup queue row.
- Start only the old writer through the QA supervisor; publish one
  root-owned forward pending token, stop writer, create newest
  WAL-consistent SQLite rescue, store guest boot ID, leave pending.

After FIRST actual LXC114 reboot:
- Demand a different guest boot ID, preserved exact Docker image
  IDs/container IDs and restart=no for both writer containers.
- Refuse any already-running writer; require exact forward token
  and matching newest SQLite/WAL/PHOTO and Telegram intent history.
- Prove both supervisor and direct Docker start cannot write while
  the pending marker remains set.
- Clear only the matching forward token, start the distinct new
  image, verify old rows preserved and L1/L2/L3 each grow 1->2.
- Publish the independent reverse pending token, stop new writer,
  create another newest rescue, store the second guest boot ID.

After SECOND actual LXC114 reboot:
- Require a third distinct boot ID and the same two image IDs.
- Require exact reverse token, no running writer, complete rescue.
- Again prove direct Docker start fails under pending.
- Clear only matching reverse token, resume old image, verify
  full history and each phase grows 2->3.
- Remove ONLY the disposable QA containers, image tags, database,
  root fence and rescue files.

## Actual evidence

All three modes passed on LXC114 across two real pct reboot 114
operations. The local image IDs persisted through both guest boots:
- old sha256:3d011bc958918f69529504dbb734392d500d2b8c59dac725292907cac307cee5
- new sha256:93121114c43d958211b3d9791b7c6522d5de0d9598c310bfbb055d9258bbccd7

The guest boot IDs observed after the two reboots were
9c30b6d4-507e-4a59-bb2a-1030a585ec38 and
a374f28e-625b-4018-ae52-0b713eb674dc.

Old database contents were preserved as an exact prefix throughout.
Both SQLite quick_check and three-phase parity passed. The PHOTO
identity, uncertain Telegram send intent, chat message ID and
status_cleanup queue fields stayed unchanged. No Telegram API call,
meter connection, image pull or production file modification
occurred. No Docker writer restarted autonomously at reboot.

After the final QA cleanup: pending absent, no running QA writer,
no QA image tags, no temporary QA database or rescue directory.
Targeted tests 22/22 PASS, Ruff0.14.2 and project quality gate PASS.

## Strict limits

These two local images contain IDENTICAL application code with
different image labels and image IDs. They are not two independently
published/signed real releases. Positive source+OCI proof in the
other QA rehearsals remains explicitly mocked. No actual signed
source TAR + OCI transition or release rollback was authorized.
The live system still has an unguarded legacy --recover startup
and a competing Docker restart owner.

Before production: acquire a real new signed source TAR/Sigstore
bundle and matching signed OCI index, enforce the sole systemd
owner and authenticated deployment receipt, then rehearse the
actual cross-version migration/rollback with newest WAL, PHOTO,
Telegram intents and guest restart.

## QA-only commands

    bash tests/qa_offline_image_switch.sh --help
    bash tests/qa_offline_image_switch.sh --qa-reboot-prepare
    # reboot only isolated LXC114 via Proxmox host
    bash tests/qa_offline_image_switch.sh --qa-reboot-forward
    # reboot only isolated LXC114 again
    bash tests/qa_offline_image_switch.sh --qa-reboot-reverse

If a QA stage is interrupted, inspect first; explicit
--qa-reboot-cleanup removes only the named disposable QA resources.
Never invoke these commands against production.
