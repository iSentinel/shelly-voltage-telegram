# Issues 340/422/431: bounded external public-OCI checks

Date: 2026-11-19, Europe/Kyiv
Scope: future source-release GitHub Actions only.

## Problem

The tag-only source release now checks public signed OCI before it
creates GitHub Release, but a retry count alone does not bound a
single hanging Cosign process or registry request.

## Source-only fix

The required "Check public signed OCI before source publication"
step now bounds each independent command with GNU coreutils timeout:
- 45 seconds for anonymous GHCR tag resolution and digest hash;
- 70 seconds for the independent public Cosign verify on that digest;
- 90 seconds for both-platform SPDX/SLSA/Git revision metadata check.

All probes still require exact source SHA, tag, GitHub workflow
identity, repository and OIDC issuer. The existing 12-attempt
bounded retry gives image-build time to publish its signature.
All failed or timed-out attempts ultimately fail the release
job with status 7, before any GitHub Release is created. No
weakened signature fallback or ignored verification error exists.

## Evidence

- Isolated QA Bash mock execution: timeout on signature call and
  timeout on registry index call both reliably exit 7 without
  emitting PAIR READY or allowing source publication.
- Full source-release policy and helper suite 42/42 PASS.
- Ruff0.14.2 formatting and maintainability quality gate PASS.
- Workflow YAML parser and exact Bash -n PASS.
- No production Docker, systemd, SQLite, Telegram or public
  source/image release changed by this task.

A success here is a workflow-policy test, not actual proof that a
new source TAR, Sigstore bundle and OCI image pair were published.
