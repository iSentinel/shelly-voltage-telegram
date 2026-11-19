# Issues 340/422/431: actual local Docker image-ID switch and reverse

Date: 2026-11-19, Europe/Kyiv
Status: LXC114 QA-only acceptance. No signed new release deployed.

## What was actually tested

A completely offline root-only test was performed in the
shelly-docker-reboot-qa LXC114 guest. It built two distinct
local Docker image IDs from one existing local, non-production
runtime base (svt-qa-verified-local:0.5.21-hw) using labels
old=v0.5.25-hw and new=v0.5.26-hw. Both images ran the SAME
application code. Neither image was published, signed, nor
pulled from GHCR. This tests Docker switching mechanics,
NOT binary/source/schema compatibility of distinct real releases.

The test created only disposable QA database, root-owned fence,
private newest-history rescues and Docker containers:
- Docker network:none, readonly rootfs, UID10001, restart:no,
  cap-drop ALL, no-new-privileges and source mounted readonly;
- old image launched through the same sole-owner QA supervisor;
- published a real root-owned pending token, stopped old writer,
  created a WAL-consistent newest SQLite rescue and verified
  complete L1/L2/L3, PHOTO and Telegram pending/cleanup data;
- verified supervisor refused to start new image while pending;
- controlled clearing of the exact forward token then launched a
  container with a DIFFERENT Docker image ID;
- verified all original measurements remained a prefix of the
  new DB history, and L1/L2/L3 each grew from one row to two;
- published a new rollback pending token, stopped the new image,
  captured the latest WAL-consistent rescue, verified reverse
  supervisor startup was blocked under pending;
- cleared only the exact reverse token, relaunched the old
  Docker image and confirmed L1/L2/L3 grew to four rows each;
- checked full original history prefix, SQLite PRAGMA quick_check,
  unchanged PHOTO ID, pending Telegram send and cleanup queue
  with needs_unpin and retry_at, at both transition boundaries.

Two distinct immutable local Docker image IDs were confirmed:
- old image ID sha256:aff38c93ab6e68e19160b320af9fd34ab64ee8d197e336de184c1d199388fbbf
- new image ID sha256:fa5f85474bca1fe0bb88acf3f8afb89422601320a8ca4d30efa6598c429d893f

All real QA steps PASS. Containers, tags and temporary paths were
removed at the end. No network or Telegram API access occurred.
The live signed v0.5.25-hw production container remained untouched.

## Guardrails and repeatability

The QA-only tests/qa_offline_image_switch.sh refuses any host other
than shelly-docker-reboot-qa or non-root invocation and refuses
preexisting named QA paths, containers and image tags.
The script supports --help and cleans only literal QA resources.
Its static policy and read-only cutover preflight unit tests
15/15 PASS with strict ResourceWarning policy, Ruff0.14.2 and
maintainability/quality gate PASS.

## Still blocked

This does not prove a migration between two NEW signed source
and OCI versions. The app code is identical between these local
images and the positive signed-source proof used in other joined
QA tests is MOCKED. An actual signed old/new source+OCI manifest,
new binary/schema compatibility, production rollback with
newest WAL/PHOTO/Telegram intents, sole systemd owner cutover,
and hard-process/guest-reboot acceptance remain required.

In particular do NOT install the candidate .unit, run legacy
apply-update.sh --recover against production, move the signed
QA receipt into LXC110, or infer production release authorization
from this test.

## QA-only commands

    bash tests/qa_offline_image_switch.sh --help
    bash tests/qa_offline_image_switch.sh
    python3 -m unittest -q test_qa_offline_image_switch_policy

The executable test is root-only in disconnected LXC114.
